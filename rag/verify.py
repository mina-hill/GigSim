"""Conservative local claim verification with source spans and quoted evidence."""

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from .corpus import Evidence

ClaimStatus = Literal["supported", "partly_supported", "unsupported"]
_CITATION = re.compile(r"\[evidence:([a-z0-9_./-]+)\]", re.IGNORECASE)
_PARAGRAPH = re.compile(r"\S.*?(?=\n\s*\n|\Z)", re.DOTALL)
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])|\n+")
_TERM = re.compile(r"[a-z0-9]+(?:[.#_+-][a-z0-9]+)*(?:[%+#]+)?", re.IGNORECASE)
_NON_CLAIMS = frozenset({"hi", "hello", "thanks", "thanks for your time", "thank you"})


@dataclass(frozen=True, slots=True)
class ClaimVerification:
    text: str
    start: int
    end: int
    status: ClaimStatus
    evidence_ids: tuple[str, ...] = ()
    quote: str | None = None


@dataclass(frozen=True, slots=True)
class _Excerpt:
    evidence_id: str
    text: str
    terms: tuple[str, ...]


def _terms(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", _CITATION.sub("", text)).casefold()
    terms = tuple(_TERM.findall(normalized))
    if terms and terms[0] in {"i", "we"}:
        return terms[1:]
    return terms


def _sentences(text: str) -> Iterator[tuple[int, int, str]]:
    start = 0
    for delimiter in _BOUNDARY.finditer(text):
        end = delimiter.start()
        if text[start:end].strip():
            yield start, end, text[start:end]
        start = delimiter.end()
    if text[start:].strip():
        yield start, len(text), text[start:]


def _excerpts(evidence: Sequence[Evidence]) -> list[_Excerpt]:
    passages = []
    for item in evidence:
        if item.type == "past_proposal":
            continue
        portfolio_ref = f"My portfolio includes {item.title}"
        passages.append(_Excerpt(item.id, item.title, _terms(portfolio_ref)))
        if item.type == "case_study" and item.skills:
            described_skills = f"Case study skills include {', '.join(item.skills)}"
            passages.append(_Excerpt(item.id, described_skills, _terms(described_skills)))
        for _, _, sentence in _sentences(item.text):
            passages.append(_Excerpt(item.id, sentence, _terms(sentence)))
    return passages


def _evaluate_claim(
    text: str, start: int, end: int, excerpts: Sequence[_Excerpt]
) -> ClaimVerification:
    terms = _terms(text)
    partial: _Excerpt | None = None
    best = 0.0
    for passage in excerpts:
        if terms == passage.terms:
            return ClaimVerification(
                text, start, end, "supported", (passage.evidence_id,), passage.text
            )
        overlap = len(set(terms) & set(passage.terms)) / max(1, len(set(terms)))
        if len(terms) >= 4 and overlap >= 0.65 and overlap > best:
            partial, best = passage, overlap

    if partial is not None:
        return ClaimVerification(
            text, start, end, "partly_supported", (partial.evidence_id,), partial.text
        )
    return ClaimVerification(text, start, end, "unsupported")


def verify_claims(
    draft: str, evidence: Sequence[Evidence], *, planned_text: Sequence[str] = ()
) -> list[ClaimVerification]:
    """Check claim sentences against cited, persona-scoped primary evidence.

    Exact normalized source text can establish support. A template writer may identify its
    fixed non-claim text through planned_text. Unrecognized, paraphrased or extended claims
    require human review. Past proposals are never used as proof that a project was delivered.
    """
    excerpts = _excerpts(evidence)
    non_claims = _NON_CLAIMS | {
        " ".join(_terms(sentence)) for text in planned_text for _, _, sentence in _sentences(text)
    }
    verified = []
    for paragraph in _PARAGRAPH.finditer(draft):
        text = paragraph.group()
        citations = set(_CITATION.findall(text))
        scope = [item for item in excerpts if not citations or item.evidence_id in citations]
        for start, end, sentence in _sentences(text):
            terms = _terms(sentence)
            if not terms or " ".join(terms) in non_claims:
                continue
            verified.append(
                _evaluate_claim(sentence, paragraph.start() + start, paragraph.start() + end, scope)
            )
    return verified


def remove_unsupported(draft: str, claims: Sequence[ClaimVerification]) -> str:
    """Remove unsupported and partially supported claims before presenting the draft."""
    remove = sorted(
        (claim for claim in claims if claim.status != "supported"), key=lambda c: c.start
    )
    parts: list[str] = []
    cursor = 0
    for claim in remove:
        parts.append(draft[cursor : claim.start])
        cursor = claim.end
    parts.append(draft[cursor:])
    return re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", "".join(parts)).strip()
