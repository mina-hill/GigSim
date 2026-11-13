"""Load GigSim policy files and compute the Connects cost of a proposal."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

POLICY_DIR = Path(__file__).resolve().parent / "policy"


@lru_cache(maxsize=16)
def load_policy(version: str) -> dict[str, Any]:
    path = POLICY_DIR / f"{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No GigSim policy {version!r} at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def available_versions() -> list[str]:
    return sorted(p.stem for p in POLICY_DIR.glob("*.yaml"))


def _condition_matches(cond: dict[str, Any], job: dict[str, Any]) -> bool:
    for key, value in cond.items():
        if key == "job_age_hours_gt" and not job["age_hours"] > value:
            return False
        if key == "applicants_gt" and not job["applicants"] > value:
            return False
        if key == "domain_in" and job["domain"] not in value:
            return False
        if key == "budget_max_gt" and not (job.get("budget_max") or 0) > value:
            return False
        if key not in {"job_age_hours_gt", "applicants_gt", "domain_in", "budget_max_gt"}:
            raise ValueError(f"Unknown policy condition {key!r}")
    return True


def connects_cost(policy: dict[str, Any], job: dict[str, Any]) -> tuple[int, list[str]]:
    """Return (cost, ids of multipliers applied). `job` needs base_connects, age_hours,
    applicants, domain, budget_max."""
    cost = float(job["base_connects"])
    applied: list[str] = []
    for mult in policy.get("connects", {}).get("multipliers", []) or []:
        if _condition_matches(mult.get("when", {}), job):
            cost *= float(mult["factor"])
            applied.append(mult.get("id", "unnamed"))
    return int(round(cost)), applied
