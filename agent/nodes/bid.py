"""Offline bid services for GigSim fixtures and explicitly scoped portfolio evidence."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Self

from agent.config import REPO_ROOT, GigBidConfig, get_config
from agent.handoffs import (
    ClaimVerification,
    ComplianceVerdict,
    Critique,
    FitReport,
    JobSpec,
    PriceQuote,
    ProposalDraft,
    RiskReport,
)
from agent.nodes.analyst import analyze_job
from agent.nodes.compliance import check_proposal, load_proposal_rules
from agent.nodes.critic import critique_proposal
from agent.nodes.fit_matcher import PortfolioEvidence, match_job_fit
from agent.nodes.pricing import price_job
from agent.nodes.risk_assessor import assess_job_risk
from agent.nodes.writer import verify_proposal, write_proposal
from agent.state import GigBidState
from scripts.personas.render import load_bible


class OfflineBidNodes:
    """Deterministic services for the bid workflow without a model provider or live tools."""

    def __init__(
        self,
        evidence: Iterable[PortfolioEvidence],
        *,
        minimum_project_budget: float,
        minimum_hourly_rate: float,
        preferred_hourly_rate: float,
        config: GigBidConfig | None = None,
    ) -> None:
        settings = config if config is not None else get_config()
        self.evidence = tuple(evidence)
        self.evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(self.evidence_by_id) != len(self.evidence):
            raise ValueError("Portfolio evidence ids must be unique")
        self.minimum_project_budget = minimum_project_budget
        self.minimum_hourly_rate = minimum_hourly_rate
        self.preferred_hourly_rate = preferred_hourly_rate
        self.connects_budget = int(settings.connects["default_weekly_budget"])
        policy_path = REPO_ROOT / Path(settings.policy_pack["rules_path"])
        self.rules = load_proposal_rules(policy_path)

    @classmethod
    def from_sample(cls, config: GigBidConfig | None = None) -> Self:
        """Load the single synthetic freelancer and evidence item from the starter kit."""
        sample = REPO_ROOT / "data" / "sample"
        freelancer, = json.loads((sample / "freelancers.json").read_text(encoding="utf-8"))
        items = json.loads((sample / "portfolio_items.json").read_text(encoding="utf-8"))
        evidence = [
            PortfolioEvidence(
                evidence_id=item["evidence_id"],
                title=item["title"],
                summary=item["summary"],
                skills=tuple(item["skills"]),
            )
            for item in items if item["freelancer_id"] == freelancer["freelancer_id"]
        ]
        return cls(
            evidence,
            minimum_project_budget=freelancer["minimum_project_budget_usd"],
            minimum_hourly_rate=freelancer["hourly_rate_usd"],
            preferred_hourly_rate=freelancer["hourly_rate_usd"],
            config=config,
        )

    @classmethod
    def from_persona(
        cls, persona: Literal["ai_ml", "data_analyst", "fullstack"],
        config: GigBidConfig | None = None,
    ) -> Self:
        """Load a named synthetic persona for a GigSim fixture run."""
        if persona not in ("ai_ml", "data_analyst", "fullstack"):
            raise ValueError("Unknown GigSim persona")
        bible = load_bible(REPO_ROOT / "data" / "personas" / persona)
        evidence = [
            PortfolioEvidence(
                evidence_id=f"cs-{project.id}",
                title=project.name,
                summary=". ".join(step.rstrip(".") for step in project.approach),
                skills=tuple(project.skills_explicit + project.skills_implied),
            )
            for project in bible.projects
        ]
        return cls(
            evidence,
            minimum_project_budget=bible.rates["min_project_usd"],
            minimum_hourly_rate=bible.rates["floor_hourly_usd"],
            preferred_hourly_rate=bible.rates["hourly_usd"],
            config=config,
        )

    async def analyze(self, job: dict[str, Any]) -> JobSpec:
        return analyze_job(job)

    async def assess_risk(self, job: dict[str, Any]) -> RiskReport:
        return assess_job_risk(job)

    async def match_fit(self, job: dict[str, Any]) -> FitReport:
        return match_job_fit(job, self.evidence)

    async def price(self, state: GigBidState) -> PriceQuote:
        floor = state.get("price_floor", 0)
        remaining = state.get("connects_remaining", self.connects_budget)
        return price_job(
            state["job"],
            minimum_project_budget=max(floor, self.minimum_project_budget),
            minimum_hourly_rate=max(floor, self.minimum_hourly_rate),
            preferred_hourly_rate=self.preferred_hourly_rate,
            connects_budget=min(remaining, self.connects_budget),
        )

    def _selected_evidence(self, state: GigBidState) -> list[PortfolioEvidence]:
        ids = state["fit_report"]["evidence_ids"][:2]
        return [self.evidence_by_id[evidence_id] for evidence_id in ids]

    async def write(self, state: GigBidState) -> ProposalDraft:
        return write_proposal(
            state["job"]["job_id"],
            state["job"].get("screening_questions", []),
            self._selected_evidence(state),
        )

    async def verify(self, state: GigBidState) -> list[ClaimVerification]:
        draft = ProposalDraft.model_validate(state["proposal_draft"])
        return verify_proposal(draft, self._selected_evidence(state))

    async def critique(self, state: GigBidState) -> Critique:
        draft = ProposalDraft.model_validate(state["proposal_draft"])
        verification = [
            ClaimVerification.model_validate(item) for item in state["claim_verification"]
        ]
        return critique_proposal(draft, verification)

    async def check_compliance(self, state: GigBidState) -> ComplianceVerdict:
        draft = ProposalDraft.model_validate(state["proposal_draft"])
        # Re-evaluate the current text so a stale checkpointed claim report cannot pass the gate.
        return check_proposal(
            draft, await self.verify(state), state["job"].get("screening_questions", []), self.rules
        )
