"""Render and verify an offline proposal against verbatim portfolio evidence."""

import re

from agent.handoffs import ClaimVerification, ProposalDraft
from agent.nodes.fit_matcher import PortfolioEvidence
from agent.nodes.risk_assessor import is_injection

_OPENING = (
    "Hello, I would start by confirming what you want the first milestone to deliver "
    "and agreeing on how to evaluate the result."
)
_CLOSING = (
    "I can share a short plan here before we commit to the timeline, "
    "including the handover material and checks for the agreed deliverables."
)
_VALIDATION = (
    "I would agree on acceptance checks, test against a representative sample, "
    "and document the results and any limitations."
)
_IMBALANCED_CLASSIFIER = (
    "I would use a stratified holdout, compare precision and recall, "
    "and choose a threshold based on the cost of false positives and false negatives."
)
_ESTIMATE = "I would estimate the first milestone after reviewing the scope and inputs."
_TOOLS = "I would confirm the existing tooling and inputs before choosing an implementation."
_NO_ANSWER = "I can discuss the project requirements here before committing to scope."
_SAFE_PARAGRAPHS = {
    _OPENING, _CLOSING, _VALIDATION, _IMBALANCED_CLASSIFIER, _ESTIMATE, _TOOLS, _NO_ANSWER,
    "Hello,", "Thank you for considering my proposal.", "Thanks for your time.",
}


def _evidence_paragraph(item: PortfolioEvidence) -> str:
    summary = " ".join(item.summary.split()).rstrip(".")
    return f"My portfolio includes {item.title} [evidence:{item.evidence_id}]. {summary}."


def _screening_answer(question: str, evidence: list[PortfolioEvidence]) -> str:
    if is_injection(question):
        return _NO_ANSWER

    question = question.casefold()
    if "imbalanced classifier" in question:
        return _IMBALANCED_CLASSIFIER
    if evidence and re.search(r"\b(similar|built|completed|experience)\b", question):
        return _evidence_paragraph(evidence[0])
    if re.search(r"\b(testing|test|validating|validate|evaluate|validation)\b", question):
        return _VALIDATION
    if re.search(r"\b(estimate|hours|milestone)\b", question):
        return _ESTIMATE
    if re.search(r"\b(tools|technologies|tooling)\b", question):
        return _TOOLS
    return _NO_ANSWER


def write_proposal(
    job_id: str, questions: list[str], evidence: list[PortfolioEvidence]
) -> ProposalDraft:
    paragraphs = [_OPENING, *(_evidence_paragraph(item) for item in evidence), _CLOSING]
    return ProposalDraft(
        job_id=job_id,
        cover_letter="\n\n".join(paragraphs),
        evidence_ids=[item.evidence_id for item in evidence],
        screening_answers=[_screening_answer(question, evidence) for question in questions],
    )


def verify_proposal(
    draft: ProposalDraft, evidence: list[PortfolioEvidence]
) -> list[ClaimVerification]:
    reference = {_evidence_paragraph(item): item.evidence_id for item in evidence}
    reports = []
    for text in [draft.cover_letter, *draft.screening_answers]:
        for paragraph in re.split(r"\n\s*\n", text):
            paragraph = " ".join(paragraph.split())
            if not paragraph or paragraph in _SAFE_PARAGRAPHS:
                continue
            evidence_id = reference.get(paragraph)
            citations = [evidence_id] if evidence_id and evidence_id in draft.evidence_ids else []
            reports.append(ClaimVerification(
                supported=bool(citations),
                claim=paragraph,
                evidence_ids=citations,
            ))
    return reports
