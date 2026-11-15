"""Simulator accounts and durable runs for the local, single-worker web demo."""

from asyncio import Lock
from dataclasses import dataclass
from hashlib import sha256
from weakref import WeakValueDictionary

import httpx
from fastapi import HTTPException
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from agent.config import REPO_ROOT, GigBidConfig
from agent.graphs.bid_graph import build_bid_graph
from agent.handoffs import ApprovalDecision
from agent.marketplace_gateway import LocalBidMarketplace
from agent.nodes.bid import OfflineBidNodes
from apps.api.schemas.workflow import JobCard, JobRecord, PersonaId, PersonaView, RunStep, RunView
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.port import ConnectsBalance, Job
from scripts.personas.render import load_bible

PERSONAS: tuple[PersonaId, ...] = ("ai_ml", "fullstack", "data_analyst")


@dataclass(frozen=True)
class PersonaSession:
    view: PersonaView
    account_id: str
    adapter: GigSimAdapter
    graph: CompiledStateGraph


def _run_view(run: StateSnapshot, *, active: bool = False) -> RunView:
    data = dict(run.values)
    if run.interrupts:
        data["pending"] = run.interrupts[0].value
    elif run.next and not active:
        data["status"] = "needs_attention"
        data["resumable"] = True
        data["errors"] = [
            *data.get("errors", []),
            "The run paused mid-step. Start the run again to resume it.",
        ]
    return RunView.model_validate(data)


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
        saver: AsyncSqliteSaver,
        account_tokens: dict[str, str],
    ) -> None:
        self.settings = settings
        self.client = client
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
            graph = build_bid_graph(
                nodes=OfflineBidNodes.from_persona(persona, config=settings),
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

    def _config(self, persona: PersonaSession, job_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": self._thread_id(persona, job_id)}}

    def _lock(self, persona: PersonaSession, job_id: str) -> Lock:
        return self._locks.setdefault(self._thread_id(persona, job_id), Lock())

    async def _read_run(self, persona: PersonaSession, job_id: str) -> StateSnapshot:
        return await persona.graph.aget_state(self._config(persona, job_id))

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
        self, persona: PersonaSession, *, q: str | None, limit: int, cursor: int
    ) -> list[JobCard]:
        jobs = await persona.adapter.search_jobs(q=q, limit=limit, cursor=cursor)
        cards: list[JobCard] = []
        for job in jobs.items:
            thread = await self._read_run(persona, job.job_id)
            run = _run_view(thread, active=self._lock(persona, job.job_id).locked())
            cards.append(_job_card(job, run))
        return cards

    async def job(self, persona: PersonaSession, job_id: str) -> JobRecord:
        job = await persona.adapter.get_job(job_id)
        snapshot = await self._read_run(persona, job_id)
        run = await self._detailed_run(
            persona, job_id, snapshot, active=self._lock(persona, job_id).locked()
        )
        return JobRecord(**_job_card(job, run).model_dump(), run=run)

    async def get_run(self, persona: PersonaSession, job_id: str) -> RunView:
        snapshot = await self._read_run(persona, job_id)
        if not snapshot.values:
            await persona.adapter.get_job(job_id)
        return await self._detailed_run(
            persona, job_id, snapshot, active=self._lock(persona, job_id).locked()
        )

    async def start_run(self, persona: PersonaSession, job_id: str) -> RunView:
        async with self._lock(persona, job_id):
            thread = self._config(persona, job_id)
            snapshot = await persona.graph.aget_state(thread)
            if snapshot.interrupts or (snapshot.values and not snapshot.next):
                return await self._detailed_run(persona, job_id, snapshot)

            account = await self.verified_account(persona)

            if snapshot.next:
                await persona.graph.ainvoke(None, thread, durability="sync")
            else:
                job = await persona.adapter.get_job(job_id)
                proposals = await persona.adapter.list_proposals()
                already_submitted = any(
                    proposal.job_id == job_id and proposal.status == "submitted"
                    for proposal in proposals
                )
                connects_remaining = min(
                    account.balance, self.settings.connects["default_weekly_budget"]
                )
                await persona.graph.ainvoke(
                    {
                        "job_id": job_id,
                        "job": job.model_dump(mode="json"),
                        "duplicate": already_submitted,
                        "connects_remaining": connects_remaining,
                    },
                    thread,
                    durability="sync",
                )
            return await self._detailed_run(persona, job_id, await persona.graph.aget_state(thread))

    async def decide(
        self, persona: PersonaSession, job_id: str, decision: ApprovalDecision
    ) -> RunView:
        async with self._lock(persona, job_id):
            thread = self._config(persona, job_id)
            snapshot = await persona.graph.aget_state(thread)
            if not snapshot.values:
                await persona.adapter.get_job(job_id)
            if not snapshot.interrupts:
                raise HTTPException(409, "This job has no proposal awaiting approval.")
            pending = snapshot.interrupts[0]
            if pending.value["proposal_digest"] != decision.proposal_digest:
                raise HTTPException(409, "The draft changed. Review the current proposal.")
            await self.verified_account(persona)
            await persona.graph.ainvoke(
                Command(resume={pending.id: decision.model_dump(mode="json")}),
                thread,
                durability="sync",
            )
            return await self._detailed_run(persona, job_id, await persona.graph.aget_state(thread))
