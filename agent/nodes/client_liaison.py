"""Classify untrusted client messages and draft replies from trusted inputs."""

from __future__ import annotations

import re
from datetime import UTC
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from agent.handoffs import ClaimVerification, ComplianceVerdict, ProposalDraft
from agent.nodes.compliance import _PolicyRule, check_proposal
from agent.nodes.fit_matcher import PortfolioEvidence
from agent.nodes.risk_assessor import assess_job_risk, normalize_text

IntentCategory = Literal["question", "negotiation", "interview_request", "scam_signal"]
_OFF_PLATFORM = re.compile(
    r"\b(?:whatsapp|telegram|skype|discord|bank transfer|gift cards?|crypto|"
    r"outside the platform|off[- ]platform|reimburse you|test task for free)\b|"
    r"[\w.+-]+@[\w-]+\.[\w.]+",
    re.IGNORECASE,
)
_INTERVIEW = re.compile(
    r"\b(?:interview|short call|video call|time slots?|hop on a call|what times?.*free|"
    r"set up a .*call|[1-9][0-9]-minute call)\b",
    re.IGNORECASE,
)
_NEGOTIATION = re.compile(
    r"\b(?:budget|counter ?offer|discount|cheaper|rate|price|above what we planned)\b|\$",
    re.IGNORECASE,
)
_AMOUNT = re.compile(r"\$\s*(\d{1,6}(?:\.\d{1,2})?)(?![\d,.])")
_SAFE_SENTENCES = {
    "Hello,",
    "Thank you for the follow-up.",
    "Thanks for your time.",
    "Thank you for considering my proposal.",
    "I look forward to hearing from you.",
}
_QUESTION_FALLBACK = (
    "Thanks for following up. Could you share which part of the scope you would like me to "
    "clarify and what you need the first milestone to deliver?"
)
_NEGOTIATION_FALLBACK = (
    "Thanks for sharing your budget. I can revisit the scope and milestones here before "
    "confirming any change to the price."
)
_ASK_FOR_TIMES = (
    "Thanks for the invitation. Could you suggest a few times with your time zone? "
    "I can check my availability here."
)


class ConversationIntent(BaseModel):
    category: IntentCategory
    explanation: str
    flags: list[str] = Field(default_factory=list)


class InterviewSlot(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def check_range(self) -> InterviewSlot:
        if self.ends_at <= self.starts_at:
            raise ValueError("An interview slot must end after it starts.")
        return self


class NegotiationPolicy(BaseModel):
    current_bid: Decimal = Field(gt=0, le=1_000_000, max_digits=9, decimal_places=2)
    minimum_bid: Decimal = Field(gt=0, le=1_000_000, max_digits=9, decimal_places=2)
    max_discount: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def check_floor(self) -> NegotiationPolicy:
        if self.minimum_bid > self.current_bid:
            raise ValueError("The minimum bid must not exceed the current bid.")
        return self

    def allows(self, counter: Decimal) -> bool:
        floor = max(self.minimum_bid, self.current_bid * (1 - self.max_discount))
        return floor <= counter <= self.current_bid


class ReplyDraft(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    evidence_ids: list[str] = Field(default_factory=list)
    interview_slots: list[InterviewSlot] = Field(default_factory=list)


def load_message_rules(path: Path) -> tuple[_PolicyRule, ...]:
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]
    selected = tuple(
        _PolicyRule.model_validate(rule) for rule in rules if "message" in rule["applies_to"]
    )
    if not selected:
        raise ValueError("No message rules are configured.")
    return selected


def check_message(
    body: str, verification: list[ClaimVerification], rules: tuple[_PolicyRule, ...]
) -> ComplianceVerdict:
    # The policy evaluator checks text and verified claims independently of the market write.
    return check_proposal(
        ProposalDraft(job_id="conversation", cover_letter=body), verification, [], rules
    )


def classify_intent(body: str) -> ConversationIntent:
    text = normalize_text(body)
    risk = assess_job_risk({"description": text})
    flags = list(risk.flags)
    if _OFF_PLATFORM.search(text) and "off_platform_contact" not in flags:
        flags.append("off_platform_contact")
    if flags:
        return ConversationIntent(
            category="scam_signal",
            explanation="This message contains a scam, off-platform, or prompt-injection signal.",
            flags=flags,
        )
    if _INTERVIEW.search(text):
        return ConversationIntent(
            category="interview_request", explanation="The client mentions a call or time slots."
        )
    if _NEGOTIATION.search(text):
        return ConversationIntent(
            category="negotiation", explanation="The client discusses budget or pricing."
        )
    return ConversationIntent(
        category="question", explanation="The message can be handled as a project follow-up."
    )


def _evidence_paragraph(item: PortfolioEvidence) -> str:
    summary = " ".join(item.summary.split()).rstrip(".")
    return f"A related portfolio example is {item.title} [evidence:{item.evidence_id}]. {summary}."


def _evidence_for(body: str, evidence: tuple[PortfolioEvidence, ...]) -> PortfolioEvidence | None:
    question = normalize_text(body).casefold()
    matches = [
        (
            sum(
                bool(re.search(rf"(?<!\w){re.escape(skill.casefold())}(?!\w)", question))
                for skill in set(item.skills)
            ),
            item.evidence_id,
            item,
        )
        for item in evidence
    ]
    ranked = sorted(matches, key=lambda entry: (-entry[0], entry[1]))
    return ranked[0][2] if ranked and ranked[0][0] else None


def _offer_line(slot: InterviewSlot) -> str:
    start = slot.starts_at.astimezone(UTC)
    end = slot.ends_at.astimezone(UTC)
    return f"{start:%a} at {start:%H:%M} UTC to {end:%H:%M} UTC ({start:%d %b %Y})."


def draft_reply(
    body: str,
    intent: ConversationIntent,
    evidence: tuple[PortfolioEvidence, ...],
    availability: list[InterviewSlot],
    negotiation: NegotiationPolicy | None,
) -> ReplyDraft:
    if intent.category == "question":
        match = _evidence_for(body, evidence)
        if match is None:
            return ReplyDraft(body=_QUESTION_FALLBACK)
        return ReplyDraft(
            body=(
                "Thank you for the follow-up.\n\n"
                + _evidence_paragraph(match)
                + "\n\nI can confirm the timeline once we agree on the first milestone."
            ),
            evidence_ids=[match.evidence_id],
        )
    if intent.category == "negotiation":
        counters = _AMOUNT.findall(normalize_text(body))
        if negotiation is None or len(counters) != 1:
            return ReplyDraft(body=_NEGOTIATION_FALLBACK)
        counter = Decimal(counters[0])
        if not negotiation.allows(counter):
            return ReplyDraft(body=_NEGOTIATION_FALLBACK)
        amount = f"{counter:.2f}".removesuffix(".00")
        return ReplyDraft(
            body=f"Thanks for the counteroffer. I can work with ${amount} for the agreed scope."
        )
    if intent.category == "interview_request":
        slots = sorted(
            {(slot.starts_at, slot.ends_at): slot for slot in availability}.values(),
            key=lambda slot: (slot.starts_at, slot.ends_at),
        )[:3]
        if not slots:
            return ReplyDraft(body=_ASK_FOR_TIMES)
        return ReplyDraft(
            body="Thanks for the invitation. I can offer these times:\n\n"
            + "\n\n".join(_offer_line(slot) for slot in slots)
            + "\n\nPlease let me know which time works for you.",
            interview_slots=slots,
        )
    raise ValueError("A suspicious message cannot have a suggested reply.")


def _paragraphs(text: str) -> list[str]:
    return [" ".join(part.split()) for part in re.split(r"\n\s*\n", text) if part.strip()]


def verify_reply(
    body: str, reference: ReplyDraft, evidence: tuple[PortfolioEvidence, ...]
) -> tuple[ReplyDraft, list[ClaimVerification]]:
    """Keep edits to verified paragraphs and neutral courtesy phrases."""
    paragraphs = _paragraphs(body)
    safe_text = set(_paragraphs(reference.body)) | _SAFE_SENTENCES
    sources = {
        _evidence_paragraph(item): item.evidence_id
        for item in evidence
        if item.evidence_id in reference.evidence_ids
    }
    verification = [
        ClaimVerification(
            claim=paragraph,
            supported=paragraph in sources,
            evidence_ids=[sources[paragraph]] if paragraph in sources else [],
        )
        for paragraph in paragraphs
        if paragraph in sources or paragraph not in safe_text
    ]
    return (
        ReplyDraft(
            body=body,
            evidence_ids=list(
                dict.fromkeys(sources[part] for part in paragraphs if part in sources)
            ),
            interview_slots=[
                slot for slot in reference.interview_slots if _offer_line(slot) in paragraphs
            ],
        ),
        verification,
    )
