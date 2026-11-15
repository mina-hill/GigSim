"""Workspace settings, promotion records, and analytics from actual runs."""

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from agent.memory.promotion_rules import (
    OutcomeFeature,
    OutcomeObservation,
    PromotionRecord,
    StyleEdit,
    delete_outcome,
    delete_style,
    promote_outcome,
    promote_style,
)
from apps.api.schemas.workflow import RunView
from apps.api.schemas.workspace import (
    AnalyticsView,
    CategoryMetrics,
    MemoryEntry,
    PipelineCard,
    Preferences,
    ProposalMetric,
    RejectionCount,
    RemoveMemory,
)
from mcp_servers.marketplace.port import Event, Job, Proposal


class RecordedRun(BaseModel):
    job_id: str
    title: str
    domain: str
    preparation_ms: int | None = None
    rejection: str | None = None
    pending_card: PipelineCard | None = None
    style_edits: list[StyleEdit] = Field(default_factory=list)
    style_recorded_at: str | None = None


def when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def weekly_connects(proposals: list[Proposal], next_refill: str) -> int:
    end = when(next_refill)
    start = end - timedelta(days=7)
    return sum(
        proposal.connects_spent
        for proposal in proposals
        if proposal.submitted_at is not None and start <= when(proposal.submitted_at) < end
    )


def redact_reason(text: str) -> str:
    redacted = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
    redacted = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[payment details]", redacted)
    redacted = re.sub(r"\+?\d[\d\s().-]{8,}\d", "[phone]", redacted)
    return " ".join(redacted.split())[:240]


class WorkspaceState:
    def __init__(self, store: BaseStore):
        self.store = store

    async def preferences(self, tenant: str) -> Preferences:
        stored = await self.store.aget((tenant, "workspace"), "preferences")
        return Preferences.model_validate(stored.value) if stored else Preferences()

    async def set_preferences(self, tenant: str, preferences: Preferences) -> None:
        await self.store.aput(
            (tenant, "workspace"), "preferences", preferences.model_dump(mode="json")
        )

    async def runs(self, tenant: str) -> list[RecordedRun]:
        records = []
        for page in range(20):
            items = await self.store.asearch((tenant, "runs"), limit=100, offset=page * 100)
            records.extend(RecordedRun.model_validate(item.value) for item in items)
            if len(items) < 100:
                break
        return records

    async def record(
        self,
        tenant: str,
        job: Job,
        run: RunView,
        *,
        preparation_ms: int | None = None,
        initial_text: str | None = None,
    ) -> None:
        ns = (tenant, "runs")
        saved = await self.store.aget(ns, job.job_id)
        record = (
            RecordedRun.model_validate(saved.value)
            if saved
            else RecordedRun(job_id=job.job_id, title=job.title, domain=job.domain)
        )
        if run.pending:
            record.pending_card = PipelineCard(
                job_id=job.job_id,
                title=job.title,
                stage="drafted",
                bid_amount=run.pending.submission.bid_amount,
                bid_type=run.pending.submission.bid_type,
            )
            if record.preparation_ms is None:
                record.preparation_ms = preparation_ms
        else:
            record.pending_card = None
        if run.approval_decision and run.approval_status == "rejected":
            record.rejection = redact_reason(run.approval_decision.reason or "")
        if initial_text and run.outcome and run.approval_status == "approved":
            record.style_edits = self._approved_styles(
                job.job_id, initial_text, run.outcome.cover_letter
            )
            record.style_recorded_at = datetime.now(UTC).isoformat()
        await self.store.aput(ns, job.job_id, record.model_dump(mode="json"))
        if record.style_edits:
            history = sorted(await self.runs(tenant), key=lambda item: item.style_recorded_at or "")
            edits = [edit for item in history for edit in item.style_edits]
            for rule in {edit.rule for edit in record.style_edits}:
                await promote_style(self.store, tenant, rule, edits)

    @staticmethod
    def _approved_styles(job_id: str, initial: str, sent: str) -> list[StyleEdit]:
        greeting = re.compile(r"^(?:hello|hi|dear)\b", re.I)
        checks = {
            "omit_greeting": (bool(greeting.search(initial)), not greeting.search(sent)),
            "shorter_opening": (
                bool(initial.split("\n\n", 1)[0]),
                len(sent.split("\n\n", 1)[0].split()) + 5
                < len(initial.split("\n\n", 1)[0].split()),
            ),
            "avoid_passionate": (
                "passionate" in initial.casefold(),
                "passionate" not in sent.casefold(),
            ),
        }
        return [
            StyleEdit(
                edit_id=f"{job_id}:{rule}",
                job_id=job_id,
                rule=rule,
                approved=True,
                supports=bool(edited),
            )
            for rule, (possible, edited) in checks.items()
            if possible
        ]

    async def memory(self, tenant: str) -> list[MemoryEntry]:
        entries = []
        for kind in ("style", "outcome"):
            for item in await self.store.asearch((tenant, kind), limit=100):
                if item.value.get("deleted"):
                    continue
                value = PromotionRecord.model_validate(item.value)
                entries.append(
                    MemoryEntry(
                        kind=kind,
                        category=item.namespace[2] if kind == "outcome" else None,
                        subject=value.subject,
                        rule_id=value.rule_id,
                        observations=len(value.source_ids),
                    )
                )
        return entries

    async def remove_memory(self, tenant: str, target: RemoveMemory) -> bool:
        entries = await self.memory(tenant)
        if not any(
            (item.kind, item.category, item.subject)
            == (target.kind, target.category, target.subject)
            for item in entries
        ):
            return False
        if target.kind == "style":
            await delete_style(self.store, tenant, target.subject)
        else:
            feature, value = target.subject.split(":", 1)
            await delete_outcome(
                self.store, tenant, target.category or "", OutcomeFeature(kind=feature, value=value)
            )
        return True

    async def analytics(
        self,
        tenant: str,
        proposals: list[Proposal],
        events: list[Event],
        *,
        history_complete: bool,
    ) -> AnalyticsView:
        records = {item.job_id: item for item in await self.runs(tenant)}
        groups: dict[str, CategoryMetrics] = {}
        observations: dict[str, list[OutcomeObservation]] = {}
        replied = {event.proposal_id for event in events if event.type == "client_replied"}
        metrics = []
        for proposal in proposals:
            if proposal.status != "submitted":
                continue
            record = records.get(proposal.job_id)
            domain = record.domain if record else "unclassified"
            group = groups.setdefault(domain, CategoryMetrics(domain=domain))
            won = proposal.stage == "hired"
            had_reply = proposal.proposal_id in replied or proposal.stage in {
                "replied",
                "interview",
                "hired",
            }
            group.submitted += 1
            group.replied += had_reply
            group.hired += won
            group.connects_spent += proposal.connects_spent
            metrics.append(
                ProposalMetric(
                    job_id=proposal.job_id,
                    title=record.title if record else proposal.job_id,
                    proposal_id=proposal.proposal_id,
                    preparation_ms=record.preparation_ms if record else None,
                    provider_cost_usd=0 if record and record.preparation_ms is not None else None,
                    connects_spent=proposal.connects_spent,
                )
            )
            if not record or (not had_reply and proposal.stage != "lost"):
                continue
            words = len(proposal.cover_letter.split())
            bucket = "short" if words < 100 else "medium" if words < 225 else "long"
            observations.setdefault(domain, []).append(
                OutcomeObservation(
                    outcome_id=proposal.proposal_id,
                    feature=OutcomeFeature(kind="length_bucket", value=bucket),
                    outcome="won" if won else "replied" if had_reply else "ignored",
                )
            )
        if history_complete:
            for domain, items in observations.items():
                baseline = sum(item.outcome != "ignored" for item in items) / len(items)
                for feature in {item.feature.value: item.feature for item in items}.values():
                    await promote_outcome(
                        self.store,
                        tenant,
                        domain,
                        feature,
                        items,
                        baseline_reply_rate=baseline,
                    )
        reasons = Counter(item.rejection for item in records.values() if item.rejection)
        return AnalyticsView(
            categories=sorted(groups.values(), key=lambda item: item.domain),
            top_rejections=[
                RejectionCount(reason=reason, count=count)
                for reason, count in reasons.most_common(5)
            ],
            proposals=metrics,
            event_history_complete=history_complete,
        )

    async def pipeline(self, tenant: str, proposals: list[Proposal]) -> list[PipelineCard]:
        records = {item.job_id: item for item in await self.runs(tenant)}
        visible_jobs = {item.job_id for item in proposals}
        cards = [
            item.pending_card
            for item in records.values()
            if item.pending_card is not None and item.job_id not in visible_jobs
        ]
        for proposal in proposals:
            record = records.get(proposal.job_id)
            stage = self.pipeline_stage(proposal)
            cards.append(
                PipelineCard(
                    job_id=proposal.job_id,
                    title=record.title if record else proposal.job_id,
                    proposal_id=proposal.proposal_id,
                    stage=stage,
                    bid_amount=proposal.bid_amount,
                    bid_type=proposal.bid_type,
                    connects_spent=proposal.connects_spent,
                    created_at=proposal.submitted_at or proposal.created_at,
                )
            )
        return cards

    @staticmethod
    def pipeline_stage(
        proposal: Proposal,
    ) -> Literal["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"]:
        if proposal.status == "draft":
            return "drafted"
        if proposal.stage in ("viewed", "replied", "interview", "hired", "lost"):
            return proposal.stage
        return "submitted"
