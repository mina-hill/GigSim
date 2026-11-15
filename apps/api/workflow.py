"""Simulator accounts and durable, persona-scoped workflow runs."""

from asyncio import Lock
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter
from weakref import WeakValueDictionary

import httpx
from arq.connections import ArqRedis
from arq.jobs import Job as QueueJob
from arq.jobs import JobStatus
from fastapi import HTTPException
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from agent.config import REPO_ROOT, GigBidConfig
from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.handoffs import ApprovalDecision
from agent.marketplace_gateway import LocalBidMarketplace
from agent.nodes.bid import OfflineBidNodes
from agent.state import BidGraphInput
from apps.api.schemas.conversation import ConversationRunView, StartConversation
from apps.api.schemas.workflow import JobCard, JobRecord, PersonaId, PersonaView, RunStep, RunView
from apps.api.streaming import BidProgress, BidProgressEvent
from apps.api.workspace import WorkspaceState, weekly_connects
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.port import ConnectsBalance, Job, PaymentType
from scripts.personas.render import load_bible

PERSONAS: tuple[PersonaId, ...] = ("ai_ml", "fullstack", "data_analyst")


@dataclass(frozen=True)
class PersonaSession:
    view: PersonaView
    account_id: str
    adapter: GigSimAdapter
    graph: CompiledStateGraph
    conversation: CompiledStateGraph


def _run_view(run: StateSnapshot, *, active: bool = False) -> RunView:
    data = dict(run.values)
    if run.interrupts:
        data["pending"] = run.interrupts[0].value
    elif active and (not data or run.next):
        data["status"] = "running"
    elif run.next and not active:
        data["status"] = "needs_attention"
        data["resumable"] = True
        data["errors"] = [
            *data.get("errors", []),
            "The run paused mid-step. Start the run again to resume it.",
        ]
    return RunView.model_validate(data)


def _conversation_view(snapshot: StateSnapshot) -> ConversationRunView:
    data = dict(snapshot.values)
    if snapshot.interrupts:
        data["pending"] = snapshot.interrupts[0].value
    elif snapshot.next:
        data["status"] = "needs_attention"
        data["resumable"] = True
    return ConversationRunView.model_validate(data)


def _job_card(job: Job, run: RunView) -> JobCard:
    return JobCard(
        **job.model_dump(),
        fit_score=run.fit_report.score if run.fit_report else None,
        risk_score=run.risk_report.score if run.risk_report else None,
        action=run.decision.action if run.decision else None,
        run_status=run.status,
    )


class WebWorkflow:
    def __init__(
        self,
        settings: GigBidConfig,
        client: httpx.AsyncClient,
        saver: BaseCheckpointSaver,
        account_tokens: dict[str, str],
        workspace: WorkspaceState,
        *,
        redis: ArqRedis | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.workspace = workspace
        self.redis = redis
        self.progress = BidProgress(redis)
        self.realm = sha256(str(client.base_url).encode()).hexdigest()
        self.sessions: dict[str, PersonaSession] = {}
        self._locks: WeakValueDictionary[str, Lock] = WeakValueDictionary()
        if settings.marketplace["mode"] != "gigsim":
            return

        for persona in PERSONAS:
            bible = load_bible(REPO_ROOT / "data" / "personas" / persona)
            token = account_tokens.get(bible.account_id)
            if token is None:
                continue
            adapter = GigSimAdapter(client, token=token)
            nodes = OfflineBidNodes.from_persona(persona, config=settings)
            graph = build_bid_graph(
                nodes=nodes,
                marketplace=LocalBidMarketplace(adapter),
                checkpointer=saver,
                config=settings,
            )
            self.sessions[persona] = PersonaSession(
                PersonaView(
                    id=persona,
                    name=bible.name,
                    headline=bible.headline,
                    rate=bible.rates["hourly_usd"],
                ),
                bible.account_id,
                adapter,
                graph,
                build_conversation_graph(
                    marketplace=adapter,
                    checkpointer=saver,
                    portfolio_evidence=nodes.evidence,
                    config=settings,
                ),
            )

    def _require_simulator(self) -> None:
        if self.settings.marketplace["mode"] != "gigsim":
            raise HTTPException(503, "The web demo requires GigSim mode.")

    def list_personas(self) -> list[PersonaView]:
        self._require_simulator()
        return [session.view for session in self.sessions.values()]

    def persona(self, persona_id: str) -> PersonaSession:
        self._require_simulator()
        if persona_id not in PERSONAS:
            raise HTTPException(404, "Persona not found.")
        if persona_id not in self.sessions:
            raise HTTPException(503, "The selected persona has no configured GigSim account.")
        return self.sessions[persona_id]

    def _thread_id(self, persona: PersonaSession, job_id: str) -> str:
        return f"{self.realm}:{persona.account_id}:{job_id}"

    def tenant(self, persona: PersonaSession) -> str:
        return f"{self.realm}:{persona.account_id}"

    def _config(self, persona: PersonaSession, job_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": self._thread_id(persona, job_id)}}

    def _lock(self, persona: PersonaSession, job_id: str) -> Lock:
        return self._locks.setdefault(self._thread_id(persona, job_id), Lock())

    async def _read_run(self, persona: PersonaSession, job_id: str) -> StateSnapshot:
        return await persona.graph.aget_state(self._config(persona, job_id))

    def queue_id(self, persona: PersonaSession, job_id: str) -> str:
        return "bid:" + self._thread_id(persona, job_id)

    def bid_event_key(self, persona: PersonaSession, job_id: str) -> str:
        return "run:" + self._thread_id(persona, job_id)

    async def _run_bid_graph(
        self,
        persona: PersonaSession,
        job_id: str,
        thread: RunnableConfig,
        data: BidGraphInput | Command | None,
    ) -> None:
        key = self.bid_event_key(persona, job_id)
        await self.progress.publish(key, BidProgressEvent(kind="run", status="started"))
        try:
            async for chunk in persona.graph.astream(
                data, thread, stream_mode="tasks", version="v2", durability="sync"
            ):
                event = BidProgressEvent.for_task(chunk["data"])
                if event is not None:
                    await self.progress.publish(key, event)
        except Exception:
            await self.progress.publish(key, BidProgressEvent(kind="run", status="error"))
            raise
        await self.progress.publish(key, BidProgressEvent(kind="run", status="completed"))

    async def _active(self, persona: PersonaSession, job_id: str, snapshot: StateSnapshot) -> bool:
        if self._lock(persona, job_id).locked():
            return True
        if self.redis is None or snapshot.interrupts or (snapshot.values and not snapshot.next):
            return False
        status = await QueueJob(self.queue_id(persona, job_id), redis=self.redis).status()
        return status in {JobStatus.queued, JobStatus.deferred, JobStatus.in_progress}

    async def _detailed_run(
        self, persona: PersonaSession, job_id: str, snapshot: StateSnapshot, *, active: bool = False
    ) -> RunView:
        run = _run_view(snapshot, active=active)
        if not snapshot.values:
            return run

        current_step = (snapshot.metadata or {}).get("step")
        timeline: list[RunStep] = []
        history = persona.graph.aget_state_history(self._config(persona, job_id), limit=40)
        async for checkpoint in history:
            step = (checkpoint.metadata or {}).get("step")
            tasks = [task for task in checkpoint.tasks if not task.name.startswith("__")]
            if step is None or not tasks or (current_step is not None and step > current_step):
                continue
            if any(task.error for task in tasks):
                status = "error"
            elif step != current_step:
                status = "completed"
            else:
                status = "waiting_for_approval" if snapshot.interrupts else "scheduled"
            timeline.append(
                RunStep(
                    step=step,
                    created_at=checkpoint.created_at,
                    nodes=[task.name for task in tasks],
                    status=status,
                )
            )
        run.timeline = list(reversed(timeline))
        return run

    async def verified_account(self, persona: PersonaSession) -> ConnectsBalance:
        account = await persona.adapter.get_connects_balance()
        if account.account_id != persona.account_id:
            raise HTTPException(503, "The simulator account does not match the selected persona.")
        return account

    async def list_jobs(
        self,
        persona: PersonaSession,
        *,
        q: str | None,
        limit: int,
        cursor: int,
        domain: str | None = None,
        payment_type: PaymentType | None = None,
    ) -> list[JobCard]:
        jobs = await persona.adapter.search_jobs(
            q=q,
            domain=domain,
            payment_type=payment_type,
            limit=limit,
            cursor=cursor,
        )
        cards: list[JobCard] = []
        for job in jobs.items:
            thread = await self._read_run(persona, job.job_id)
            run = _run_view(thread, active=await self._active(persona, job.job_id, thread))
            cards.append(_job_card(job, run))
        return cards

    async def job(self, persona: PersonaSession, job_id: str) -> JobRecord:
        job = await persona.adapter.get_job(job_id)
        snapshot = await self._read_run(persona, job_id)
        run = await self._detailed_run(
            persona, job_id, snapshot, active=await self._active(persona, job_id, snapshot)
        )
        return JobRecord(**_job_card(job, run).model_dump(), run=run)

    async def get_run(self, persona: PersonaSession, job_id: str) -> RunView:
        snapshot = await self._read_run(persona, job_id)
        if not snapshot.values:
            await persona.adapter.get_job(job_id)
        return await self._detailed_run(
            persona, job_id, snapshot, active=await self._active(persona, job_id, snapshot)
        )

    async def schedule_run(self, persona: PersonaSession, job_id: str) -> RunView:
        if self.redis is None:
            return await self.start_run(persona, job_id)
        snapshot = await self._read_run(persona, job_id)
        if snapshot.interrupts or (snapshot.values and not snapshot.next):
            return await self._detailed_run(persona, job_id, snapshot)
        await persona.adapter.get_job(job_id)
        await self.redis.enqueue_job(
            "run_bid", persona.view.id, job_id, _job_id=self.queue_id(persona, job_id), _expires=600
        )
        return _run_view(await self._read_run(persona, job_id), active=True)

    async def start_run(self, persona: PersonaSession, job_id: str) -> RunView:
        async with self._lock(persona, job_id):
            thread = self._config(persona, job_id)
            snapshot = await persona.graph.aget_state(thread)
            if snapshot.interrupts or (snapshot.values and not snapshot.next):
                return await self._detailed_run(persona, job_id, snapshot)

            account = await self.verified_account(persona)
            started = perf_counter()

            if snapshot.next:
                await self._run_bid_graph(persona, job_id, thread, None)
            else:
                job = await persona.adapter.get_job(job_id)
                proposals = await persona.adapter.list_proposals()
                already_submitted = any(
                    proposal.job_id == job_id and proposal.status == "submitted"
                    for proposal in proposals
                )
                preferences = await self.workspace.preferences(self.tenant(persona))
                budget = preferences.weekly_connects_budget
                if budget is None:
                    budget = self.settings.connects["default_weekly_budget"]
                connects_remaining = min(
                    account.balance,
                    max(0, budget - weekly_connects(proposals, account.next_refill_at)),
                )
                auto_skip = preferences.auto_skip_low_fit
                if auto_skip is None:
                    auto_skip = self.settings.autonomy.auto_skip_low_fit
                style_rules = [
                    item.subject
                    for item in await self.workspace.memory(self.tenant(persona))
                    if item.kind == "style"
                ]
                await self._run_bid_graph(
                    persona,
                    job_id,
                    thread,
                    {
                        "job_id": job_id,
                        "job": job.model_dump(mode="json"),
                        "duplicate": already_submitted,
                        "connects_remaining": connects_remaining,
                        "preferred_hourly_rate": preferences.preferred_hourly_rate,
                        "auto_skip_low_fit": auto_skip,
                        "style_rules": style_rules,
                        "portfolio_items": [
                            {
                                "evidence_id": "portfolio-"
                                + sha256(item.title.encode()).hexdigest()[:16],
                                **item.model_dump(mode="json"),
                            }
                            for item in preferences.portfolio
                        ],
                    },
                )
            updated = await persona.graph.aget_state(thread)
            run = await self._detailed_run(persona, job_id, updated)
            if updated.values.get("job"):
                await self.workspace.record(
                    self.tenant(persona),
                    Job.model_validate(updated.values["job"]),
                    run,
                    preparation_ms=None
                    if snapshot.next
                    else round((perf_counter() - started) * 1000),
                )
            return run

    async def decide(
        self, persona: PersonaSession, job_id: str, decision: ApprovalDecision
    ) -> RunView:
        async with self._lock(persona, job_id), self._lock(persona, "weekly-connects"):
            thread = self._config(persona, job_id)
            snapshot = await persona.graph.aget_state(thread)
            if not snapshot.values:
                await persona.adapter.get_job(job_id)
            if not snapshot.interrupts:
                raise HTTPException(409, "This job has no proposal awaiting approval.")
            pending = snapshot.interrupts[0]
            if pending.value["proposal_digest"] != decision.proposal_digest:
                raise HTTPException(409, "The draft changed. Review the current proposal.")
            account = await self.verified_account(persona)
            original = None
            if decision.action == "approve":
                preferences = await self.workspace.preferences(self.tenant(persona))
                budget = preferences.weekly_connects_budget
                if budget is None:
                    budget = self.settings.connects["default_weekly_budget"]
                proposals = await persona.adapter.list_proposals()
                remaining = budget - weekly_connects(proposals, account.next_refill_at)
                if pending.value["preview"]["connects_cost"] > remaining:
                    raise HTTPException(
                        409, "The draft exceeds your remaining weekly Connects budget."
                    )
                original = pending.value["draft"]["cover_letter"]
                if pending.value["draft_version"] > 1:
                    async for history in persona.graph.aget_state_history(thread, limit=40):
                        values = history.values
                        if values.get("draft_version") == 1 and values.get("proposal_draft"):
                            original = values["proposal_draft"]["cover_letter"]
                            break
            await self._run_bid_graph(
                persona,
                job_id,
                thread,
                Command(resume={pending.id: decision.model_dump(mode="json")}),
            )
            updated = await persona.graph.aget_state(thread)
            run = await self._detailed_run(persona, job_id, updated)
            if updated.values.get("job"):
                await self.workspace.record(
                    self.tenant(persona),
                    Job.model_validate(updated.values["job"]),
                    run,
                    initial_text=original,
                )
            return run

    async def conversation_run(
        self, persona: PersonaSession, thread_id: str
    ) -> ConversationRunView:
        await self.verified_account(persona)
        await persona.adapter.list_messages(thread_id)
        thread = self._config(persona, f"conv:{thread_id}")
        return _conversation_view(await persona.conversation.aget_state(thread))

    async def start_conversation(
        self, persona: PersonaSession, thread_id: str, options: StartConversation
    ) -> ConversationRunView:
        key = f"conv:{thread_id}"
        thread = self._config(persona, key)
        async with self._lock(persona, key):
            await self.verified_account(persona)
            snapshot = await persona.conversation.aget_state(thread)
            if snapshot.interrupts:
                return _conversation_view(snapshot)
            if snapshot.next:
                data = None
            else:
                data = {"thread_id": thread_id, **options.model_dump(mode="json")}
            await persona.conversation.ainvoke(data, thread, durability="sync")
            return _conversation_view(await persona.conversation.aget_state(thread))

    async def decide_conversation(
        self, persona: PersonaSession, thread_id: str, decision: ApprovalDecision
    ) -> ConversationRunView:
        key = f"conv:{thread_id}"
        thread = self._config(persona, key)
        async with self._lock(persona, key):
            await self.verified_account(persona)
            snapshot = await persona.conversation.aget_state(thread)
            if not snapshot.interrupts:
                await persona.adapter.list_messages(thread_id)
                raise HTTPException(409, "This conversation has no reply awaiting approval.")
            pending = snapshot.interrupts[0]
            if pending.value["proposal_digest"] != decision.proposal_digest:
                raise HTTPException(409, "The reply changed. Review the current draft.")
            await persona.conversation.ainvoke(
                Command(resume={pending.id: decision.model_dump(mode="json")}),
                thread,
                durability="sync",
            )
            return _conversation_view(await persona.conversation.aget_state(thread))
