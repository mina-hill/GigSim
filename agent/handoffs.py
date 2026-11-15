from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobSpec(BaseModel):
    job_id: str
    title: str
    deliverables: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    budget_min: float | None = None
    budget_max: float | None = None


class RiskReport(BaseModel):
    score: float = Field(ge=0, le=1)
    flags: list[str] = Field(default_factory=list)
    explanation: str


class FitReport(BaseModel):
    score: float = Field(ge=0, le=1)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ProposalDraft(BaseModel):
    job_id: str
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    status: str = "pending_review"


class BidDecision(BaseModel):
    action: Literal["skip", "watch", "bid"]
    rule_ids: list[str]


class PriceQuote(BaseModel):
    amount: float = Field(ge=0, allow_inf_nan=False)
    bid_type: Literal["fixed", "hourly"] = "fixed"
    connects_cost: int = Field(ge=0)
    worth_it: bool = True
    rationale: str = ""


class ClaimVerification(BaseModel):
    supported: bool
    claim: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class Critique(BaseModel):
    needs_revision: bool
    feedback: list[str] = Field(default_factory=list)


class ComplianceVerdict(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["approve", "edit", "reject"]
    reason: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=5000)
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_action_details(self) -> "ApprovalDecision":
        if self.action == "reject" and not self.reason:
            raise ValueError("Enter a reason to reject the proposal.")
        if self.action == "edit" and not self.text:
            raise ValueError("Enter the revised proposal text.")
        return self
