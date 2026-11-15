"""Evaluate the configured policy pack on the outgoing proposal."""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from agent.handoffs import ClaimVerification, ComplianceVerdict, ProposalDraft
from agent.nodes.risk_assessor import normalize_text


class _PolicyRule(BaseModel):
    id: str
    severity: Literal["warn", "block"]
    applies_to: list[Literal["proposal", "message"]]
    reason: str
    pattern: str | None = None
    check: Literal[
        "unsupported_claims", "screening_answers_present", "cover_letter_length"
    ] | None = None
    min_chars: int = Field(default=0, ge=0)
    max_chars: int = Field(default=5000, ge=0)

    @model_validator(mode="after")
    def validate_check(self) -> "_PolicyRule":
        if (self.pattern is None) == (self.check is None):
            raise ValueError("A rule must specify one pattern or named check")
        if self.pattern is not None:
            re.compile(self.pattern, re.IGNORECASE)
        if self.min_chars > self.max_chars:
            raise ValueError("A policy rule has reversed length limits")
        return self


def load_proposal_rules(path: Path) -> tuple[_PolicyRule, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = tuple(_PolicyRule.model_validate(rule) for rule in raw["rules"])
    proposal_rules = tuple(rule for rule in rules if "proposal" in rule.applies_to)
    if not proposal_rules:
        raise ValueError("No proposal rules are configured")
    return proposal_rules


def check_proposal(
    draft: ProposalDraft,
    verification: list[ClaimVerification],
    questions: list[str],
    rules: tuple[_PolicyRule, ...],
) -> ComplianceVerdict:
    text = normalize_text("\n".join([draft.cover_letter, *draft.screening_answers]))
    unsupported = any(not claim.supported for claim in verification)
    incomplete = (
        len(draft.screening_answers) != len(questions)
        or any(not answer.strip() for answer in draft.screening_answers)
    )
    reasons: list[str] = []
    warnings: list[str] = []
    for rule in rules:
        if rule.pattern is not None:
            violated = re.search(rule.pattern, text, re.IGNORECASE) is not None
        elif rule.check == "unsupported_claims":
            violated = unsupported
        elif rule.check == "screening_answers_present":
            violated = incomplete
        else:
            violated = not rule.min_chars <= len(draft.cover_letter.strip()) <= rule.max_chars
        if violated:
            (reasons if rule.severity == "block" else warnings).append(rule.reason)

    if unsupported and not reasons:
        reasons.append("Every experience claim must cite portfolio evidence.")
    return ComplianceVerdict(allowed=not reasons, reasons=reasons, warnings=warnings)
