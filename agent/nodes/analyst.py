"""Read the marketplace's structured job fields for the offline bid workflow."""

from math import isfinite
from typing import Any

from agent.handoffs import JobSpec


def job_skills(job: dict[str, Any]) -> list[str]:
    skills = job.get("skills", job.get("required_skills", []))
    unique = {skill.casefold(): skill for skill in skills if skill.strip()}
    return list(unique.values())


def job_budget(job: dict[str, Any]) -> tuple[float | None, float | None]:
    if job.get("payment_type") == "hourly":
        lower, upper = job.get("hourly_min"), job.get("hourly_max")
    elif isinstance(job.get("budget"), dict):
        budget = job["budget"]
        if budget.get("currency", "USD") != "USD":
            return None, None
        lower, upper = budget.get("min"), budget.get("max")
    else:
        lower, upper = job.get("budget_min"), job.get("budget_max")

    return _valid_amount(lower), _valid_amount(upper)


def _valid_amount(value: Any) -> float | None:
    if type(value) in (int, float) and isfinite(value) and 0 <= value <= 1_000_000:
        return float(value)
    return None


def analyze_job(job: dict[str, Any]) -> JobSpec:
    lower, upper = job_budget(job)
    return JobSpec(
        job_id=job["job_id"],
        title=job.get("title", ""),
        deliverables=job.get("deliverables", []),
        required_skills=job_skills(job),
        budget_min=lower,
        budget_max=upper,
    )
