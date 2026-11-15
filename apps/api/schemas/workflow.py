"""Public views of marketplace data and checkpointed bids."""

from typing import Literal

from pydantic import BaseModel, Field, computed_field

from agent.handoffs import (
    ApprovalDecision,
    BidDecision,
    ClaimVerification,
    ComplianceVerdict,
    Critique,
    FitReport,
    JobSpec,
    PriceQuote,
    ProposalDraft,
    RiskReport,
)
from agent.marketplace_gateway import BidPreview
from agent.nodes.writer import alternate_opening
from mcp_servers.marketplace.port import Job, Proposal, ProposalInput

PersonaId = Literal["ai_ml", "fullstack", "data_analyst"]
RunStatus = Literal["idle", "running", "awaiting_approval", "needs_attention", "skipped", "done"]


class PersonaView(BaseModel):
    id: PersonaId
    name: str
    headline: str
    rate: float


class PendingApproval(BaseModel):
    draft: ProposalDraft
    draft_version: int
    claim_verification: list[ClaimVerification]
    risk_report: RiskReport
    price_quote: PriceQuote
    submission: ProposalInput
    preview: BidPreview
    proposal_digest: str
    error: str | None = None

    @computed_field
    @property
    def alternate_opening(self) -> str | None:
        return alternate_opening(self.submission.cover_letter)


class RunStep(BaseModel):
    step: int
    created_at: str | None
    nodes: list[str]
    status: Literal["scheduled", "completed", "waiting_for_approval", "error"]


class RunView(BaseModel):
    status: RunStatus = "idle"
    resumable: bool = False
    pending: PendingApproval | None = None
    decision: BidDecision | None = None
    job_spec: JobSpec | None = None
    risk_report: RiskReport | None = None
    fit_report: FitReport | None = None
    proposal_draft: ProposalDraft | None = None
    claim_verification: list[ClaimVerification] = Field(default_factory=list)
    price_quote: PriceQuote | None = None
    critique: Critique | None = None
    compliance_verdict: ComplianceVerdict | None = None
    approval_status: Literal["pending", "approved", "edited", "rejected"] | None = None
    approval_decision: ApprovalDecision | None = None
    outcome: Proposal | None = None
    errors: list[str] = Field(default_factory=list)
    steps: int = 0
    revisions: int = 0
    timeline: list[RunStep] = Field(default_factory=list)


class JobCard(Job):
    fit_score: float | None = None
    risk_score: float | None = None
    action: Literal["skip", "watch", "bid"] | None = None
    run_status: RunStatus


class JobRecord(JobCard):
    run: RunView


class ErrorView(BaseModel):
    detail: str


class HealthView(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["gigbid-api"] = "gigbid-api"


class DependencyStatus(BaseModel):
    status: str
    detail: str | None = None


class DependencyView(BaseModel):
    gigsim: DependencyStatus
    model_profile: DependencyStatus
    marketplace_mode: DependencyStatus
