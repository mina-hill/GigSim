"""Generate a persona's history of past proposals, each labelled won, replied or ignored.

Each proposal answers a job built from one of the persona's projects (or an off-domain job),
using a recorded strategy: opening style, kind of evidence cited, length and price position.
Outcomes come from GigSim's own client rules on a randomly drawn client, plus 12% label noise,
so "what worked" is learnable from outcomes but not perfectly (as with real histories).
The label source is stored on every row and documented in docs/DATA_CARD.md.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from gigsim.clients import score_proposal

LABEL_NOISE = 0.12
LABEL_SOURCE = "gigsim-rules-v1 + 12% label noise"

GENERIC_OPENINGS = [
    "Dear Hiring Manager, I am writing to apply for your {title} job.",
    "Hello sir, I have read your job post carefully and I am the perfect fit for it.",
    "Greetings! I'm a passionate {role} with {years} years of experience.",
    "I am very interested in your job and I am the best candidate for this work.",
]
SPECIFIC_OPENINGS = [
    "{need_cap} is the problem I solved on {alias} for a {industry} client.",
    "You need {need}; on {alias} I did exactly that, and I can show you the numbers.",
    "Your post mentions {skill}; that was the core of {alias}, which I delivered last year.",
]
QUESTION_OPENINGS = [
    "Before anything else: is your priority accuracy or speed of delivery? On {alias} that "
    "choice shaped the whole design.",
    "Quick question first: do you already have the {skill} part in place, or should I set it "
    "up? On {alias} I did both.",
]
EVIDENCE_METRIC = "On {alias} I used {skills} and {approach}; the result: {metric} {value}."
EVIDENCE_PLAIN = "I built similar work on {alias} with {skills}, where I {approach}."
EVIDENCE_IRRELEVANT = "I also built {alias} for a {industry} client, which went very well."
EVIDENCE_NONE = ("I have strong experience in {skills} and I always deliver high quality work "
                 "on time.")
PLAN = ("My plan: start with {step1}, then {step2}, with a check-in after the first milestone so "
        "you can see progress early.")
FLUFF = ("I am a hard-working professional who always meets deadlines and communicates clearly. "
         "Client satisfaction is my top priority and I am available for long-term work.")
CLOSINGS = ["Happy to walk you through the repo on a short call.", "I can start this week.",
            "Looking forward to hearing from you.", "Thanks for reading."]

OFF_DOMAIN_JOBS = [
    ("Logo and brand kit for a bakery", ["logo design", "illustrator"], "design"),
    ("WordPress theme tweaks for a blog", ["wordpress", "php"], "web publishing"),
    ("Translate 20 pages from English to Spanish", ["translation", "spanish"], "translation"),
    ("Edit weekly YouTube videos", ["video editing", "premiere pro"], "media"),
    ("Monthly bookkeeping in QuickBooks", ["bookkeeping", "quickbooks"], "accounting"),
    ("2D art assets for a mobile game", ["2d art", "illustration"], "games"),
    ("Cold calling for a real estate agency", ["sales", "cold calling"], "real estate"),
    ("Write SEO blog posts about travel", ["copywriting", "seo"], "travel"),
]
ROLE = {"ai_ml": "machine learning engineer", "fullstack": "full-stack developer",
        "data_analyst": "data analyst"}


def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16))


def _weighted(r: random.Random, options: dict[str, float]) -> str:
    return r.choices(list(options), weights=list(options.values()), k=1)[0]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def build_job_bank(bible: Any, r: random.Random) -> list[dict[str, Any]]:
    jobs = []
    industries = sorted({p.client_industry for p in bible.projects if p.client})
    for p in bible.projects:
        if p.decoy_of:
            continue
        skills = p.skills_explicit[:3]
        for variant in range(2):
            industry = p.client_industry if variant == 0 else r.choice(industries)
            title = (f"{p.name} for a {industry} company" if variant == 0
                     else f"Looking for a {skills[0]} expert ({industry})")
            need = _lower_first(p.problem.rstrip("."))
            budget = round(bible.rates["min_project_usd"] * r.uniform(1.2, 8), -1)
            jobs.append({
                "title": title,
                "description": f"We are a {industry} business. Right now {need}. We are looking "
                               f"for someone with {', '.join(skills)} experience to fix this.",
                "skills": skills, "industry": industry, "source_project": p.id,
                "category": bible.persona_id, "payment_type": "fixed", "budget_max": budget,
                "screening_questions": [], "hidden": {
                    "must_have_skills": [s.lower() for s in skills[:2]]},
            })
    for title, skills, industry in OFF_DOMAIN_JOBS:
        jobs.append({
            "title": title, "description": f"{title}. Must have {', '.join(skills)} experience.",
            "skills": skills, "industry": industry, "source_project": None,
            "category": "off_domain", "payment_type": "fixed",
            "budget_max": round(r.uniform(100, 900), -1), "screening_questions": [],
            "hidden": {"must_have_skills": skills[:1]},
        })
    return jobs


def compose(bible: Any, job: dict[str, Any], strategy: dict[str, str],
            r: random.Random) -> tuple[str, list[str]]:
    by_id = {p.id: p for p in bible.projects}
    real = [p for p in bible.projects if not p.decoy_of]
    source = by_id.get(job["source_project"]) if job["source_project"] else None
    matched = source or r.choice(real)
    other = r.choice([p for p in real if p.id != matched.id])
    skill = job["skills"][0]
    need = _lower_first(job["description"].split("Right now ")[-1].split(".")[0])
    fill = {"title": job["title"], "role": ROLE.get(bible.persona_id, "freelancer"),
            "years": bible.years_experience, "alias": matched.aliases[0], "need": need,
            "need_cap": need[:1].upper() + need[1:], "industry": matched.client_industry,
            "skill": skill}
    parts: list[str] = []
    cited: list[str] = []
    opening = strategy["opening"]
    if opening == "generic":
        parts.append(r.choice(GENERIC_OPENINGS).format(**fill))
    elif opening == "specific":
        parts.append(r.choice(SPECIFIC_OPENINGS).format(**fill))
        cited.append(matched.id)
    else:
        parts.append(r.choice(QUESTION_OPENINGS).format(**fill))
        cited.append(matched.id)

    evidence = strategy["evidence"]
    if evidence == "relevant_metric":
        res = matched.results[0]
        parts.append(EVIDENCE_METRIC.format(alias=matched.aliases[0],
                                            skills=" and ".join(matched.skills_explicit[:2]),
                                            approach=_lower_first(matched.approach[0]),
                                            metric=res.metric, value=res.value))
        cited.append(matched.id)
    elif evidence == "relevant_plain":
        parts.append(EVIDENCE_PLAIN.format(alias=matched.aliases[0],
                                           skills=" and ".join(matched.skills_explicit[:2]),
                                           approach=_lower_first(matched.approach[0])))
        cited.append(matched.id)
    elif evidence == "irrelevant":
        parts.append(EVIDENCE_IRRELEVANT.format(alias=other.aliases[0],
                                                industry=other.client_industry))
        cited.append(other.id)
    else:
        parts.append(EVIDENCE_NONE.format(skills=", ".join(bible.skills["core"][:4])))

    if strategy["length"] in {"medium", "long"}:
        steps = matched.approach + ["a short written handover"]
        parts.append(PLAN.format(step1=_lower_first(steps[0]), step2=_lower_first(steps[1])))
    if strategy["length"] == "long":
        res = other.results[0]
        parts.append(f"Before that, on {other.aliases[0]}, I {_lower_first(other.approach[0])} "
                     f"({res.metric}: {res.value}).")
        cited.append(other.id)
        parts.append(FLUFF)
    parts.append(r.choice(CLOSINGS))
    return "\n\n".join(parts), sorted(set(cited))


def generate_past_proposals(bible: Any, n: int = 150, seed: int = 4016) -> list[dict[str, Any]]:
    r = _rng(bible.persona_id, "past-proposals", str(seed))
    jobs = build_job_bank(bible, r)
    rows = []
    for i in range(n):
        job = r.choice(jobs)
        strategy = {
            "opening": _weighted(r, {"generic": 0.35, "specific": 0.45, "question": 0.20}),
            "evidence": _weighted(r, {"relevant_metric": 0.35, "relevant_plain": 0.25,
                                      "irrelevant": 0.20, "none": 0.20}),
            "length": _weighted(r, {"short": 0.30, "medium": 0.45, "long": 0.25}),
            "price": _weighted(r, {"low": 0.30, "mid": 0.45, "high": 0.25}),
        }
        letter, cited = compose(bible, job, strategy, r)
        factor = {"low": 0.75, "mid": 0.92, "high": 1.15}[strategy["price"]]
        bid = round(job["budget_max"] * factor, -1)
        prefs = {
            "dislikes_generic_opening": r.random() < 0.7,
            "preferred_length": r.choice(["short", "medium", "long"]),
            "responsiveness": r.uniform(0.4, 1.0), "price_tolerance": r.uniform(1.0, 1.2),
            "strictness": r.uniform(0.3, 0.8),
        }
        pid = f"pp-{bible.persona_id}-{i:03d}"
        decision = score_proposal(job=job, prefs=prefs, cover_letter=letter,
                                  screening_answers=[], bid_amount=bid, proposal_id=pid,
                                  seed=seed)
        if decision.outcome == "interview":
            outcome = "won" if r.random() < 0.55 else "replied"
        elif decision.outcome == "replied" and not decision.ghost:
            outcome = "replied"
        else:
            outcome = "ignored"
        noisy = r.random() < LABEL_NOISE
        if noisy:
            outcome = r.choice([o for o in ("won", "replied", "ignored") if o != outcome])
        year = r.choice([2022, 2023, 2024, 2025, 2026])
        month = r.randint(1, 9 if year == 2026 else 12)
        rows.append({
            "id": pid, "type": "past_proposal", "persona": bible.persona_id,
            "date": f"{year}-{month:02d}", "job_title": job["title"],
            "job_description": job["description"], "job_skills": job["skills"],
            "job_category": job["category"], "client_industry": job["industry"],
            "cover_letter": letter, "bid_amount": bid, "outcome": outcome,
            "cited_project_ids": cited, "strategy": strategy, "label_source": LABEL_SOURCE,
            "label_noise_applied": noisy, "synthetic": True,
        })
    return rows
