"""Deterministic risk signals for job posts, including hidden prompt instructions."""

import re
import unicodedata
from typing import Any

from agent.handoffs import RiskReport

_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_INJECTION = re.compile(
    r"(?im)^\s*(?:system|developer|assistant)\s*:|"
    r"\b(?:ai assistant|ignore (?:all |your |the )?(?:previous |prior )?"
    r"(?:instructions|rules)|disregard (?:the |your )?(?:guidelines|rules|instructions)|"
    r"previous instructions are outdated|skip (?:the )?(?:approval|review) step)\b"
)
_SCAM = {
    "unpaid_work": re.compile(
        r"\bunpaid (?:test|trial)|\b(?:test task|dashboard) for free\b", re.IGNORECASE
    ),
    "off_platform_contact": re.compile(
        r"\b(?:message|contact|text) (?:me|us|the client) on "
        r"(?:whatsapp|telegram|discord|skype|an external messaging app)\b|"
        r"\bwhatsapp me\b",
        re.IGNORECASE,
    ),
    "pay_to_start": re.compile(
        r"\b(?:must buy|you must purchase|pay (?:a )?(?:deposit|fee))\b", re.IGNORECASE
    ),
    "overpayment": re.compile(
        r"\b(?:check|cheque)\b[^.!?]{0,160}\bforward the rest\b", re.IGNORECASE
    ),
    "credential_request": re.compile(r"\b(?:username and password|login credentials)\b", re.I),
}


def normalize_text(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text)
        if unicodedata.category(char) != "Cf"
    )


def is_injection(text: str) -> bool:
    return _INJECTION.search(normalize_text(text)) is not None


def assess_job_risk(job: dict[str, Any]) -> RiskReport:
    sections = [job.get("title", ""), job.get("description", "")]
    sections.extend(job.get("screening_questions", []))
    text = normalize_text("\n".join(sections))
    visible_text = _HTML_COMMENT.sub(" ", text)
    flags = [flag for flag, pattern in _SCAM.items() if pattern.search(visible_text)]
    score = 1.0 if flags else 0.0
    if is_injection(text):
        flags.append("prompt_injection")
        score = max(score, 0.25)
    return RiskReport(
        score=score,
        flags=flags,
        explanation=", ".join(flags) or "No configured risk signals matched.",
    )
