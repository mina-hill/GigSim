"""Stored workspace preferences and read models for the simulator."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from agent.nodes.risk_assessor import is_injection


class SavedSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=60)
    query: str = Field(default="", max_length=200)
    domain: str = Field(default="", max_length=40, pattern=r"^[a-z0-9_]*$")
    payment_type: Literal["any", "fixed", "hourly"] = "any"
    interval_minutes: Literal[30, 60, 180] = 60
    enabled: bool = False


class UploadedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=20, max_length=1200)
    skills: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    ] = Field(
        min_length=1,
        max_length=12,
    )

    @model_validator(mode="after")
    def portfolio_is_evidence(self) -> "UploadedEvidence":
        if is_injection(" ".join([self.title, self.summary, *self.skills])):
            raise ValueError("Portfolio evidence cannot contain prompt instructions.")
        return self


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_skip_low_fit: bool | None = None
    weekly_connects_budget: int | None = Field(default=None, ge=0, le=1000)
    preferred_hourly_rate: float | None = Field(default=None, ge=1, le=10000, allow_inf_nan=False)
    saved_searches: list[SavedSearch] = Field(default_factory=list, max_length=5)
    portfolio: list[UploadedEvidence] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique_searches(self) -> "Preferences":
        names = {item.name.casefold() for item in self.saved_searches}
        if len(names) != len(self.saved_searches):
            raise ValueError("Saved search names must be unique.")
        if len({item.title.casefold() for item in self.portfolio}) != len(self.portfolio):
            raise ValueError("Portfolio titles must be unique.")
        return self


class WorkspaceSettings(BaseModel):
    preferences: Preferences
    default_weekly_budget: int
    default_hourly_rate: float
    default_auto_skip: bool
    marketplace: Literal["gigsim"] = "gigsim"
    account_id: str
    weekly_connects_spent: int
    connects_balance: int


class MemoryEntry(BaseModel):
    kind: Literal["style", "outcome"]
    category: str | None = None
    subject: str
    rule_id: str
    observations: int


class RemoveMemory(BaseModel):
    kind: Literal["style", "outcome"]
    category: str | None = Field(default=None, max_length=80)
    subject: str = Field(min_length=1, max_length=100)


class CategoryMetrics(BaseModel):
    domain: str
    submitted: int = 0
    replied: int = 0
    hired: int = 0
    connects_spent: int = 0


class RejectionCount(BaseModel):
    reason: str
    count: int


class ProposalMetric(BaseModel):
    job_id: str
    title: str
    proposal_id: str | None = None
    preparation_ms: int | None = None
    provider_cost_usd: float | None = None
    connects_spent: int = 0


class AnalyticsView(BaseModel):
    categories: list[CategoryMetrics] = Field(default_factory=list)
    top_rejections: list[RejectionCount] = Field(default_factory=list)
    proposals: list[ProposalMetric] = Field(default_factory=list)
    event_history_complete: bool


class PipelineCard(BaseModel):
    job_id: str
    title: str
    proposal_id: str | None = None
    stage: Literal["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"]
    bid_amount: float | None = None
    bid_type: str | None = None
    connects_spent: int = 0
    created_at: str | None = None
