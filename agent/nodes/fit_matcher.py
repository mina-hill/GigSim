"""Compare required skills with the evidence scoped to this offline bid workflow."""

from dataclasses import dataclass
from typing import Any

from agent.handoffs import FitReport
from agent.nodes.analyst import job_skills


@dataclass(frozen=True)
class PortfolioEvidence:
    evidence_id: str
    title: str
    summary: str
    skills: tuple[str, ...]


def match_job_fit(job: dict[str, Any], evidence: tuple[PortfolioEvidence, ...]) -> FitReport:
    skills = job_skills(job)
    wanted = {skill.casefold() for skill in skills}
    ranked = []
    matched: set[str] = set()
    for item in evidence:
        overlap = wanted.intersection(skill.casefold() for skill in item.skills)
        if overlap:
            matched.update(overlap)
            ranked.append((len(overlap), item.evidence_id))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return FitReport(
        score=len(matched) / len(skills) if skills else 0.0,
        matched_skills=[skill for skill in skills if skill.casefold() in matched],
        missing_skills=[skill for skill in skills if skill.casefold() not in matched],
        evidence_ids=[evidence_id for _, evidence_id in ranked],
    )
