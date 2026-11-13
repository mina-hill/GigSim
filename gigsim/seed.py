"""Load a GigSim "world" (a folder of JSONL files) into the database.

World layout (produced by scripts/data/build_world.py, or the bundled fixtures/mini_world):

    freelancers.jsonl     one account per line
    clients.jsonl         clients with public stats and hidden_prefs
    past_contracts.jsonl  client history
    jobs.jsonl            open and closed jobs; `hidden` holds must-have skills and ceilings
    market_stats.json     optional; otherwise computed from closed jobs

Usage:  uv run python -m gigsim.seed --world data/processed/gigsim_world [--db sqlite:///...]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gigsim import db
from gigsim.models import Account, Client, Job, MarketStat, PastContract, SimState
from gigsim.settings import get_settings


class WorldFreelancer(BaseModel):
    account_id: str
    display_name: str
    persona: str = ""
    weekly_connects: int = 80
    connects_balance: int | None = None


class WorldClient(BaseModel):
    client_id: str
    name: str
    country: str = ""
    industry: str = ""
    payment_verified: bool = True
    member_since: str = ""
    total_spent: float = 0.0
    rating: float | None = None
    reviews_count: int = 0
    hire_rate: float | None = None
    jobs_posted: int = 0
    enterprise: bool = False
    hidden_prefs: dict[str, Any] = Field(default_factory=dict)


class WorldContract(BaseModel):
    client_id: str
    title: str
    amount: float = 0.0
    payment_type: str = "fixed"
    rating_given: float | None = None
    review: str = ""
    freelancer_feedback: str = ""
    ended_at: str = ""


class WorldJob(BaseModel):
    job_id: str
    client_id: str
    title: str
    description: str
    skills: list[str] = Field(default_factory=list)
    domain: str = "other"
    experience_level: str = "intermediate"
    payment_type: Literal["fixed", "hourly"] = "fixed"
    budget_min: float | None = None
    budget_max: float | None = None
    hourly_min: float | None = None
    hourly_max: float | None = None
    duration: str = ""
    screening_questions: list[str] = Field(default_factory=list)
    applicants: int = 0
    base_connects: int = 8
    status: Literal["open", "closed"] = "open"
    release_tick: int = 0
    posted_offset_hours: int = 2
    hidden: dict[str, Any] = Field(default_factory=dict)
    provenance: str = ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def compute_market_stats(jobs: list[WorldJob]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for j in jobs:
        if j.status != "closed":
            continue
        if j.payment_type == "fixed" and j.budget_max:
            value = j.budget_max
        elif j.payment_type == "hourly" and (j.hourly_max or j.hourly_min):
            lo = j.hourly_min or j.hourly_max
            hi = j.hourly_max or j.hourly_min
            value = (lo + hi) / 2
        else:
            continue
        groups[(j.domain, j.experience_level, j.payment_type)].append(float(value))
    rows = []
    for (domain, level, ptype), values in sorted(groups.items()):
        if len(values) < 5:
            continue
        q = statistics.quantiles(values, n=4, method="inclusive")
        rows.append({"domain": domain, "experience_level": level, "payment_type": ptype,
                     "n": len(values), "p25": q[0], "p50": q[1], "p75": q[2],
                     "source": "gigsim_history"})
    return rows


def load_world(session: Session, world: Path, *, reset: bool = True) -> dict[str, int]:
    if not world.exists():
        raise FileNotFoundError(f"World folder {world} does not exist")
    if reset:
        session.close()
        db.drop_all()
        db.create_all()
    s = get_settings()
    freelancers = [WorldFreelancer(**r) for r in _read_jsonl(world / "freelancers.jsonl")]
    clients = [WorldClient(**r) for r in _read_jsonl(world / "clients.jsonl")]
    contracts = [WorldContract(**r) for r in _read_jsonl(world / "past_contracts.jsonl")]
    jobs = [WorldJob(**r) for r in _read_jsonl(world / "jobs.jsonl")]

    session.add(SimState(id=1, tick=0, policy_version=s.policy_version, seq=0))
    for f in freelancers:
        session.add(Account(account_id=f.account_id, display_name=f.display_name,
                            persona=f.persona, weekly_connects=f.weekly_connects,
                            connects_balance=f.connects_balance
                            if f.connects_balance is not None else f.weekly_connects))
    for c in clients:
        session.add(Client(**c.model_dump()))
    session.flush()
    for k in contracts:
        session.add(PastContract(**k.model_dump()))
    for i, j in enumerate(jobs):
        data = j.model_dump()
        provenance = data.pop("provenance")
        session.add(Job(seq=i, hidden_provenance=provenance, **data))

    stats_file = world / "market_stats.json"
    stats = (json.loads(stats_file.read_text(encoding="utf-8")) if stats_file.exists()
             else compute_market_stats(jobs))
    for row in stats:
        session.add(MarketStat(domain=row["domain"], experience_level=row["experience_level"],
                               payment_type=row["payment_type"], n=int(row["n"]),
                               p25=float(row["p25"]), p50=float(row["p50"]),
                               p75=float(row["p75"]), source=row.get("source", "world")))
    session.commit()
    return {"freelancers": len(freelancers), "clients": len(clients),
            "past_contracts": len(contracts), "jobs": len(jobs), "market_stats": len(stats)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--world", default=get_settings().world_path)
    parser.add_argument("--db", default=get_settings().db_url)
    args = parser.parse_args()
    db.init_engine(args.db)
    db.create_all()
    with db.session_scope() as session:
        counts = load_world(session, Path(args.world))
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
