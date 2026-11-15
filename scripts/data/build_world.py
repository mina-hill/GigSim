"""Build the GigSim world and the gold-labelled job sets.

    uv run python -m scripts.data.build_world                 # uses data/processed/* if present
    uv run python -m scripts.data.build_world --per-persona 200

Writes:
    data/processed/gigsim_world/        world for development: historical + dev + live stream
    data/processed/gigsim_world_test/   world for the final held-out run: historical + test
    evals/datasets/dev.jsonl            gold labels for the dev jobs (committed)
    data/processed/stream_gold.jsonl    gold labels for the live-stream jobs
    $GIGBID_TEST_SPLIT_PATH             gold labels for the test jobs (outside the repo)

When the Kaggle files have been processed (scripts/data/preprocess.py), real postings are used
for historical jobs, client statistics, off-domain jobs and market price bands. Without them
everything is synthetic, so the system still runs end to end; every record says which.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.data import generators as gen
from scripts.data.sources import PROCESSED, ROOT
from scripts.personas.render import PERSONAS_DIR, load_bible

VERSION = "build_world@v1"
DOMAIN_OF = {"ai_ml": "ai_ml", "fullstack": "fullstack_web", "data_analyst": "data_analysis"}
DEFAULT_BANDS = {  # used only when no Kaggle market statistics exist
    ("ai_ml", "fixed"): (800, 4000), ("fullstack_web", "fixed"): (600, 3500),
    ("data_analysis", "fixed"): (300, 1500), ("other", "fixed"): (100, 800),
    ("ai_ml", "hourly"): (30, 70), ("fullstack_web", "hourly"): (25, 60),
    ("data_analysis", "hourly"): (18, 45), ("other", "hourly"): (10, 30),
}
COUNTRIES = ["United States", "United Kingdom", "Canada", "Australia", "Germany",
             "Netherlands", "United Arab Emirates", "Singapore", "Ireland", "Sweden"]
REVIEW_TEXT = {5: ["Excellent work, would hire again.", "Great communication and quality."],
               4: ["Good work overall, a few revisions needed.", "Solid freelancer."],
               3: ["Okay result, deadlines slipped.", "Average communication."]}
MIX = {"good": 0.40, "good_vague": 0.10, "wrong_domain": 0.12, "off_domain": 0.08,
       "below_floor": 0.08, "scam": 0.08, "injection": 0.07, "read_check": 0.02,
       "duplicate": 0.05}


def opaque_id(*parts: object) -> str:
    return "job_" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:10]


class Market:
    def __init__(self, stats: list[dict[str, Any]] | None) -> None:
        self.stats = stats or []

    def band(self, domain: str, ptype: str, level: str) -> tuple[float, float]:
        for lvl in (level, "any"):
            for s in self.stats:
                if s["domain"] == domain and s["payment_type"] == ptype and s[
                        "experience_level"] == lvl:
                    return float(s["p25"]), float(s["p75"])
        return DEFAULT_BANDS.get((domain, ptype), DEFAULT_BANDS[("other", ptype)])


class Clients:
    """Creates clients (public stats + hidden preferences) and their past contracts."""

    def __init__(self, r: random.Random, kaggle: pd.DataFrame | None, history_titles: list[str]):
        self.r, self.rows, self.contracts = r, [], []
        self.kaggle = kaggle if kaggle is not None and not kaggle.empty else None
        self.history_titles = history_titles or ["Data clean-up", "Website fixes"]
        self.by_industry: dict[str, list[str]] = {}

    def _stats(self) -> dict[str, Any]:
        r = self.r
        if self.kaggle is not None and r.random() < 0.8:
            k = self.kaggle.iloc[r.randrange(len(self.kaggle))]
            def num(v, d):  # noqa: E306
                return d if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
            return {"country": str(k.client_country or r.choice(COUNTRIES)),
                    "total_spent": num(k.client_spent, 0.0),
                    "payment_verified": bool(k.payment_verified) if k.payment_verified
                    is not None else True,
                    "rating": num(k.client_rating, None) or None,
                    "reviews_count": int(num(k.client_reviews, 0)), "source": "kaggle"}
        reviews = int(r.expovariate(1 / 8))
        return {"country": r.choice(COUNTRIES),
                "total_spent": round(r.lognormvariate(8, 1.4), -1) if reviews else 0.0,
                "payment_verified": r.random() < 0.9,
                "rating": round(r.uniform(4.0, 5.0), 1) if reviews else None,
                "reviews_count": reviews, "source": "synthetic"}

    def legit(self, industry: str) -> str:
        r = self.r
        pool = self.by_industry.setdefault(industry, [])
        if pool and r.random() < 0.3:
            return r.choice(pool)
        cid = f"cl_{len(self.rows):05d}"
        s = self._stats()
        self.rows.append({
            "client_id": cid, "name": f"Client {cid[3:]} (synthetic)", "country": s["country"],
            "industry": industry, "payment_verified": s["payment_verified"],
            "member_since": f"{r.randint(2015, 2025)}-{r.randint(1, 12):02d}-01",
            "total_spent": s["total_spent"], "rating": s["rating"],
            "reviews_count": s["reviews_count"],
            "hire_rate": round(r.uniform(0.3, 0.9), 2) if s["reviews_count"] else None,
            "jobs_posted": s["reviews_count"] + r.randint(1, 10), "enterprise": r.random() < 0.08,
            "hidden_prefs": {
                "dislikes_generic_opening": r.random() < 0.7,
                "preferred_length": r.choice(["short", "medium", "long"]),
                "responsiveness": round(r.uniform(0.4, 1.0), 2),
                "price_tolerance": round(r.uniform(1.0, 1.25), 2),
                "strictness": round(r.uniform(0.3, 0.8), 2)},
            "stats_source": s["source"],
        })
        for _ in range(min(s["reviews_count"], 6)):
            rating = r.choice([5, 5, 5, 4, 4, 3])
            self.contracts.append({
                "client_id": cid, "title": r.choice(self.history_titles),
                "amount": round(r.lognormvariate(6.5, 0.9), -1), "rating_given": float(rating),
                "review": r.choice(REVIEW_TEXT[rating]),
                "freelancer_feedback": r.choice(["Clear requirements.", "Paid on time.",
                                                 "Great client to work with."]),
                "ended_at": f"{r.randint(2022, 2026)}-{r.randint(1, 12):02d}-15"})
        pool.append(cid)
        return cid

    def scammer(self, kind: str) -> str:
        cid = f"cl_{len(self.rows):05d}"
        self.rows.append({
            "client_id": cid, "name": f"Client {cid[3:]} (synthetic)", "country": "",
            "industry": "unknown", "payment_verified": False, "member_since": "2026-11-01",
            "total_spent": 0.0, "rating": None, "reviews_count": 0, "hire_rate": None,
            "jobs_posted": 1, "enterprise": False,
            "hidden_prefs": {"scammer": True, "scam_script": kind, "responsiveness": 1.0},
            "stats_source": "synthetic"})
        return cid


def finish_job(r: random.Random, draft: dict[str, Any], *, domain: str, market: Market,
               client_id: str, prefs: dict[str, Any], persona: Any, below_floor: bool = False,
               ) -> tuple[dict[str, Any], tuple[float, float]]:
    ptype = "hourly" if r.random() < 0.3 else "fixed"
    level = r.choices(["entry", "intermediate", "expert"], [0.2, 0.55, 0.25])[0]
    p25, p75 = market.band(domain, ptype, level)
    scope = math.sqrt(max(1, draft["tags"].get("n_requirements", 3)) / 3) if ptype == "fixed" \
        else 1.0
    band = (round(p25 * scope, 2), round(p75 * scope, 2))
    job: dict[str, Any] = {
        "title": draft["title"], "description": draft["description"], "skills": draft["skills"],
        "domain": domain, "experience_level": level, "payment_type": ptype,
        "screening_questions": draft.get("screening_questions") or (
            r.sample(gen.SCREENING, r.randint(1, 2)) if r.random() < 0.4 else []),
        "applicants": int(r.expovariate(1 / 15)), "client_id": client_id,
        "duration": r.choice(["Less than 1 month", "1 to 3 months", "3 to 6 months"]),
        "provenance": draft.get("provenance", f"synthetic:{VERSION}"),
    }
    tol = float(prefs.get("price_tolerance", 1.1))
    if ptype == "fixed":
        hi = r.uniform(band[0], band[1] * 1.2)
        if below_floor:
            hi = persona.rates["min_project_usd"] * r.uniform(0.2, 0.45)
        job.update(budget_min=round(hi * 0.6, -1) or 10.0, budget_max=round(hi, -1) or 20.0,
                   hourly_min=None, hourly_max=None)
        job["hidden"] = {"must_have_skills": [s.lower() for s in draft["skills"][:2]],
                         "budget_ceiling": round(job["budget_max"] * tol, 2)}
    else:
        lo = r.uniform(band[0] * 0.8, band[0] * 1.1)
        hi = r.uniform(max(lo + 5, band[0]), band[1])
        if below_floor:
            hi = persona.rates["floor_hourly_usd"] * r.uniform(0.4, 0.7)
            lo = hi * 0.7
        job.update(budget_min=None, budget_max=None, hourly_min=round(lo, 0),
                   hourly_max=round(hi, 0))
        job["hidden"] = {"must_have_skills": [s.lower() for s in draft["skills"][:2]],
                         "hourly_ceiling": round(job["hourly_max"] * tol, 2)}
    budget_ref = job["budget_max"] or (job["hourly_max"] or 20) * 40
    job["base_connects"] = 4 if budget_ref < 300 else 8 if budget_ref < 1500 else 12 \
        if budget_ref < 5000 else 16
    return job, band


def persona_jobs(r: random.Random, persona: Any, others: list[Any], n: int, split: str,
                 market: Market, clients: Clients, scam_texts: list[dict],
                 kaggle_off: list[dict]) -> list[tuple[dict, dict]]:
    """(world job, gold row) pairs for one persona and split."""
    domain = DOMAIN_OF[persona.persona_id]
    # Jobs are built from real client-style projects; coursework, competitions and decoys
    # stay in the portfolio as evidence (and as traps) but never seed a job.
    real = [p for p in persona.projects if not p.decoy_of
            and p.client_industry not in {"none", "education"}]
    counts = {k: max(1, round(v * n)) for k, v in MIX.items()}
    counts["good"] += n - sum(counts.values())
    out: list[tuple[dict, dict]] = []

    def add(draft, *, kind, bid, skip_reason=None, is_scam=False, below=False,
            job_domain=None, duplicate_of=None, scam_kind=None):
        industry = draft.get("industry", "saas")
        cid = clients.scammer(scam_kind) if is_scam else clients.legit(industry)
        prefs = next(c for c in clients.rows if c["client_id"] == cid)["hidden_prefs"]
        job, band = finish_job(r, draft, domain=job_domain or domain, market=market,
                               client_id=cid, prefs=prefs, persona=persona, below_floor=below)
        jid = opaque_id(VERSION, split, persona.persona_id, len(out), draft["title"])
        job["job_id"] = jid
        gold = {
            "job_id": jid, "persona": persona.persona_id, "account_id": persona.account_id,
            "split": split, "kind": kind, "bid_or_skip": bid, "skip_reason": skip_reason,
            "is_scam": is_scam, "has_injection": "injection_type" in draft["tags"],
            "read_check": bool(draft["tags"].get("read_check")), "duplicate_of": duplicate_of,
            "gold_evidence": draft["gold_evidence"] if bid == "bid" or kind in {
                "injection", "read_check", "duplicate"} else [],
            "fair_price_band": list(band), "payment_type": job["payment_type"],
            "domain": job["domain"], "tags": draft["tags"],
            "label_source": draft.get("label_source", "construction"),
            "provenance": job["provenance"],
        }
        out.append((job, gold))
        return job, gold

    for _ in range(counts["good"]):
        projs = r.sample(real, 2) if r.random() < 0.3 else [r.choice(real)]
        add(gen.make_fit_job(r, projs), kind="good", bid="bid")
    for _ in range(counts["good_vague"]):
        add(gen.make_fit_job(r, [r.choice(real)], vague=True), kind="good_vague", bid="bid")
    mine = {s.lower() for p in persona.projects for s in p.skills_explicit + p.skills_implied}
    mine |= {s.lower() for group in persona.skills.values() for s in group}
    for _ in range(counts["wrong_domain"]):
        # Only projects that share no skill with this persona, so "skip" is unambiguous.
        other = r.choice(others)
        pool = [x for x in other.projects if not x.decoy_of
                and x.client_industry not in {"none", "education"}
                and not {s.lower() for s in x.skills_explicit} & mine]
        p = r.choice(pool or [x for x in other.projects if not x.decoy_of])
        d = gen.make_fit_job(r, [p])
        for _ in range(30):                       # the sampled requirements must not overlap
            if not {s.lower() for s in d["skills"]} & mine:
                break
            d = gen.make_fit_job(r, [r.choice(pool or [p])])
        d["gold_evidence"] = []
        add(d, kind="wrong_domain", bid="skip", skip_reason="skills_do_not_match",
            job_domain=DOMAIN_OF[other.persona_id])
    for _ in range(counts["off_domain"]):
        if kaggle_off and r.random() < 0.7:
            k = r.choice(kaggle_off)
            d = {"title": k["title"], "description": k["description"], "skills": k["skills"]
                 or ["General"], "industry": r.choice(gen.INDUSTRIES), "gold_evidence": [],
                 "source_projects": [], "provenance": k["provenance"],
                 "label_source": "domain_rule",
                 "tags": {"n_requirements": len(k["skills"]), "specificity": "real_post",
                          "exact_library": False, "multi_project": False}}
        else:
            d = gen.make_off_domain_job(r)
        add(d, kind="off_domain", bid="skip", skip_reason="outside_my_domain",
            job_domain="other")
    for _ in range(counts["below_floor"]):
        add(gen.make_fit_job(r, [r.choice(real)]), kind="below_floor", bid="skip",
            skip_reason="budget_below_floor", below=True)
    for _ in range(counts["scam"]):
        d = gen.make_scam_job(r, r.choice(real), scam_texts)
        add(d, kind="scam", bid="skip", skip_reason="scam", is_scam=True,
            scam_kind=d["tags"]["scam_type"])
    for _ in range(counts["injection"]):
        good = r.random() < 0.6
        base = gen.make_fit_job(r, [r.choice(real)]) if good else gen.make_off_domain_job(r)
        add(gen.plant_injection(r, base), kind="injection", bid="bid" if good else "skip",
            skip_reason=None if good else "outside_my_domain",
            job_domain=None if good else "other")
    for _ in range(counts["read_check"]):
        add(gen.add_read_check(r, gen.make_fit_job(r, [r.choice(real)])), kind="read_check",
            bid="bid")
    originals = [(j, g) for j, g in out if g["kind"] in {"good", "wrong_domain"}]
    for _ in range(counts["duplicate"]):
        oj, og = r.choice(originals)
        d = gen.make_duplicate(r, {"title": oj["title"], "description": oj["description"],
                                   "skills": oj["skills"], "gold_evidence": og["gold_evidence"],
                                   "industry": "saas", "tags": dict(og["tags"])})
        job, gold = add(d, kind="duplicate", bid="skip", skip_reason="duplicate_posting",
                        job_domain=oj["domain"], duplicate_of=oj["job_id"])
        job["client_id"] = oj["client_id"]        # a repost comes from the same client
    r.shuffle(out)
    return out


def historical_jobs(r: random.Random, kaggle: pd.DataFrame | None, n: int,
                    clients: Clients) -> list[dict[str, Any]]:
    jobs = []
    if kaggle is not None and not kaggle.empty:
        sample = kaggle.sample(n=min(n, len(kaggle)), random_state=7)
        for i, k in enumerate(sample.itertuples()):
            jobs.append({
                "job_id": opaque_id("hist", i), "client_id": clients.legit("various"),
                "title": k.title, "description": k.description, "skills": list(k.skills),
                "domain": k.domain, "experience_level": k.experience_level,
                "payment_type": k.payment_type,
                "budget_max": None if pd.isna(k.budget_max) else float(k.budget_max),
                "hourly_min": None if pd.isna(k.hourly_min) else float(k.hourly_min),
                "hourly_max": None if pd.isna(k.hourly_max) else float(k.hourly_max),
                "status": "closed", "provenance": k.provenance})
        return jobs
    for i in range(n):
        domain = r.choice(list(DOMAIN_OF.values()) + ["other"])
        ptype = "hourly" if r.random() < 0.3 else "fixed"
        lo, hi = DEFAULT_BANDS[(domain, ptype)]
        value = round(r.uniform(lo * 0.6, hi * 1.3), 0)
        jobs.append({
            "job_id": opaque_id("hist", i), "client_id": clients.legit("various"),
            "title": f"Historical {domain.replace('_', ' ')} job {i}",
            "description": "Closed historical job (synthetic) used for statistics.",
            "domain": domain, "experience_level": r.choice(["entry", "intermediate", "expert"]),
            "payment_type": ptype, "budget_max": value if ptype == "fixed" else None,
            "hourly_min": value * 0.8 if ptype == "hourly" else None,
            "hourly_max": value if ptype == "hourly" else None, "status": "closed",
            "provenance": f"synthetic:{VERSION}"})
    return jobs


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_world(folder: Path, freelancers, clients, contracts, jobs, stats) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    write_jsonl(folder / "freelancers.jsonl", freelancers)
    write_jsonl(folder / "clients.jsonl", [{k: v for k, v in c.items() if k != "stats_source"}
                                           for c in clients])
    write_jsonl(folder / "past_contracts.jsonl", contracts)
    write_jsonl(folder / "jobs.jsonl", jobs)
    if stats:
        (folder / "market_stats.json").write_text(json.dumps(stats, indent=2))


def build(per_persona: int = 200, seed: int = 4016, processed: Path = PROCESSED,
          dev_out: Path | None = None, test_out: Path | None = None) -> dict[str, Any]:
    r = random.Random(seed)
    personas = [load_bible(d) for d in sorted(PERSONAS_DIR.iterdir()) if (d / "bible.yaml")
                .exists()]
    kaggle = (pd.read_parquet(processed / "jobs_clean.parquet")
              if (processed / "jobs_clean.parquet").exists() else None)
    stats = (json.loads((processed / "market_stats.json").read_text())
             if (processed / "market_stats.json").exists() else [])
    scam_texts = ([json.loads(x) for x in (processed / "scam_texts.jsonl").read_text()
                   .splitlines() if x] if (processed / "scam_texts.jsonl").exists() else [])
    kaggle_off = [] if kaggle is None else [
        {"title": k.title, "description": k.description, "skills": list(k.skills),
         "provenance": k.provenance} for k in kaggle[kaggle.domain == "other"].head(2000)
        .itertuples()]
    history_titles = (kaggle.title.sample(min(500, len(kaggle)), random_state=3).tolist()
                      if kaggle is not None else [])
    market = Market(stats)
    clients = Clients(r, kaggle, history_titles)
    freelancers = [{"account_id": p.account_id, "display_name": f"{p.name} (synthetic)",
                    "persona": p.persona_id, "weekly_connects": 80} for p in personas]

    hist = historical_jobs(r, kaggle, 1500 if kaggle is not None else 600, clients)
    splits: dict[str, list[tuple[dict, dict]]] = {"dev": [], "stream": [], "test": []}
    sizes = {"dev": per_persona, "stream": per_persona // 2, "test": per_persona // 2}
    for split, n in sizes.items():
        for p in personas:
            others = [o for o in personas if o.persona_id != p.persona_id]
            splits[split] += persona_jobs(r, p, others, n, split, market, clients, scam_texts,
                                          kaggle_off)
    for i, (job, _) in enumerate(splits["stream"]):
        job["release_tick"] = 1 + (i // 5) * 2       # five new jobs every two simulated hours
    for job, _ in splits["dev"] + splits["test"]:
        job["release_tick"] = 0
    dev_jobs = [j for j, _ in splits["dev"]] + [j for j, _ in splits["stream"]]
    test_jobs = [j for j, _ in splits["test"]]

    write_world(processed / "gigsim_world", freelancers, clients.rows, clients.contracts,
                hist + dev_jobs, stats)
    write_world(processed / "gigsim_world_test", freelancers, clients.rows, clients.contracts,
                hist + test_jobs, stats)
    dev_out = dev_out or ROOT / "evals" / "datasets" / "dev.jsonl"
    test_out = test_out or Path(os.environ.get("GIGBID_TEST_SPLIT_PATH",
                                               processed / "test_gold.jsonl"))
    write_jsonl(dev_out, [g for _, g in splits["dev"]])
    write_jsonl(processed / "stream_gold.jsonl", [g for _, g in splits["stream"]])
    write_jsonl(test_out, [g for _, g in splits["test"]])
    summary = {
        "version": VERSION, "seed": seed, "kaggle_used": kaggle is not None,
        "market_stats_rows": len(stats), "historical_jobs": len(hist),
        "clients": len(clients.rows), "past_contracts": len(clients.contracts),
        "dev_jobs": len(splits["dev"]), "stream_jobs": len(splits["stream"]),
        "test_jobs": len(splits["test"]),
        "dev_kinds": pd.Series([g["kind"] for _, g in splits["dev"]]).value_counts().to_dict(),
        "dev_bid_share": round(sum(g["bid_or_skip"] == "bid" for _, g in splits["dev"])
                               / max(1, len(splits["dev"])), 3),
    }
    (processed / "world_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--per-persona", type=int, default=200)
    parser.add_argument("--seed", type=int, default=4016)
    args = parser.parse_args()
    print(json.dumps(build(args.per_persona, args.seed), indent=2))


if __name__ == "__main__":
    main()
