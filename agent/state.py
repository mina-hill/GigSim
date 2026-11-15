from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict


class BidGraphInput(TypedDict, total=False):
    """Inputs supplied by the worker for a new checkpoint thread."""

    job_id: str
    job: dict[str, Any]
    duplicate: bool
    connects_remaining: int
    price_floor: float


class GigBidState(BidGraphInput, total=False):
    job_spec: dict[str, Any]
    risk_report: dict[str, Any]
    fit_report: dict[str, Any]
    decision: dict[str, Any]
    evidence: list[dict[str, Any]]
    price_quote: dict[str, Any]
    proposal_draft: dict[str, Any]
    draft_version: int
    revisions: int
    claim_verification: list[dict[str, Any]]
    critique: dict[str, Any]
    compliance_verdict: dict[str, Any]
    submission: dict[str, Any] | None
    preview: dict[str, Any] | None
    proposal_digest: str | None
    approval_status: Literal["pending", "approved", "edited", "rejected"]
    approval_response: Any
    approval_decision: dict[str, Any] | None
    approval_error: str | None
    outcome: dict[str, Any]
    status: Literal["running", "awaiting_approval", "needs_attention", "skipped", "done"]
    steps: Annotated[int, add]
    errors: Annotated[list[str], add]
