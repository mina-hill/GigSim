"""Compare paired proposal openings against frozen GigSim clients without sending anything."""

import argparse
import json
from collections import defaultdict
from math import comb
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent.config import REPO_ROOT, get_config
from agent.handoffs import ClaimVerification
from agent.nodes.compliance import check_proposal, load_proposal_rules
from agent.nodes.fit_matcher import PortfolioEvidence
from agent.nodes.pricing import price_job
from agent.nodes.writer import write_proposal
from evals.runners.smoke import GOLD_LABELS, SEED, read_jsonl, stratified_sample
from gigsim.clients import score_proposal
from rag.corpus import Evidence, load_corpus
from rag.retriever import Retriever
from rag.verify import verify_claims
from scripts.data.build_world import build
from scripts.personas.render import load_bible

RESULTS = REPO_ROOT / "evals" / "results"
PERSONAS = REPO_ROOT / "data" / "personas"
COMMAND = "uv run --extra rag python -m evals.runners.x2"
PLAN = (
    "I would start by confirming the expected inputs, agreeing on an acceptance check, "
    "and sharing an early milestone for review. I can summarize the validation and handover "
    "steps before we agree on the full delivery schedule."
)
OUTCOMES = ("both_replied", "evidence_only", "question_only", "neither_replied")


def exact_mcnemar(evidence_only: int, question_only: int) -> float:
    """Two-sided exact conditional test on the discordant pairs."""
    size = evidence_only + question_only
    if not size:
        return 1.0
    lower = sum(comb(size, count) for count in range(min(evidence_only, question_only) + 1))
    return min(1.0, 2 * lower / (2**size))


def _world() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    labels = read_jsonl(GOLD_LABELS)
    eligible = [
        label
        for label in labels
        if label["kind"] in {"good", "good_vague"} and label["bid_or_skip"] == "bid"
    ]
    with TemporaryDirectory(prefix="gigbid-x2-") as directory:
        root = Path(directory)
        build(seed=SEED, processed=root, dev_out=root / "dev.jsonl", test_out=root / "test.jsonl")
        rebuilt = {label["job_id"]: label for label in read_jsonl(root / "dev.jsonl")}
        for label in eligible:
            current = rebuilt.get(label["job_id"])
            if not current or label["persona"] != current["persona"]:
                raise ValueError(f"Cannot match rebuilt job to label {label['job_id']}.")
            if label["label_source"] != "hand_verified" and label != current:
                raise ValueError(f"Construction label changed for {label['job_id']}.")
        jobs = {job["job_id"]: job for job in read_jsonl(root / "gigsim_world" / "jobs.jsonl")}
        clients = {
            client["client_id"]: client["hidden_prefs"]
            for client in read_jsonl(root / "gigsim_world" / "clients.jsonl")
        }
    return jobs, clients, stratified_sample(eligible, len(eligible))


def _draft_pair(job: dict[str, Any], evidence: Evidence, *, include_case_details: bool):
    approach = evidence.text.split("\n\n")[2]
    excerpt = approach.split(". ", 1)[0].rstrip(".")
    portfolio_item = PortfolioEvidence(evidence.id, evidence.title, approach, evidence.skills)
    questions = job["screening_questions"]
    common = write_proposal(job["job_id"], questions, [portfolio_item])
    question = (
        f"What should the first {job['skills'][0]} milestone demonstrate, "
        "and how should we measure its success?"
    )
    evidence_opening = f"My portfolio includes {evidence.title} [evidence:{evidence.id}]."
    evidence_opening += f" I {excerpt[0].lower() + excerpt[1:]}."
    body = f"{approach} [evidence:{evidence.id}]\n\n{PLAN}"
    if include_case_details:
        skills = f"Case study skills include {', '.join(evidence.skills)} [evidence:{evidence.id}]."
        outcome = evidence.text.split("\n\n")[-1].strip()
        body = f"{skills}\n\n{outcome} [evidence:{evidence.id}]\n\n{body}"
    drafts = (
        common.model_copy(update={"cover_letter": evidence_opening + "\n\n" + body}),
        common.model_copy(update={"cover_letter": question + "\n\n" + body}),
    )
    return drafts, question


def summarize(pairs: list[dict[str, Any]]) -> dict[str, int | float]:
    if not pairs:
        raise ValueError("The experiment has no comparable proposal pairs.")
    both = sum(row["evidence_reply"] and row["question_reply"] for row in pairs)
    evidence_only = sum(row["evidence_reply"] and not row["question_reply"] for row in pairs)
    question_only = sum(row["question_reply"] and not row["evidence_reply"] for row in pairs)
    neither = len(pairs) - both - evidence_only - question_only
    counts = dict(zip(OUTCOMES, (both, evidence_only, question_only, neither), strict=True))
    return {
        "pairs": len(pairs),
        **counts,
        "evidence_replies": both + evidence_only,
        "question_replies": both + question_only,
        "reply_rate_difference_pp": round(100 * (evidence_only - question_only) / len(pairs), 3),
        "exact_mcnemar_two_sided_p": round(exact_mcnemar(evidence_only, question_only), 6),
    }


def evaluate(*, include_case_details: bool = True) -> dict[str, Any]:
    jobs, clients, labels = _world()
    personas = sorted({row["persona"] for row in labels})
    retrievers = {
        name: Retriever(load_corpus(PERSONAS / name), default_variant="v8", corrective_rerank=False)
        for name in personas
    }
    rates = {name: load_bible(PERSONAS / name).rates for name in personas}
    policy = load_proposal_rules(REPO_ROOT / get_config().policy_pack["rules_path"])
    seen_clients: set[str] = set()
    exclusions: dict[str, int] = defaultdict(int)
    pairs = []
    for label in labels:
        job, persona = jobs[label["job_id"]], label["persona"]
        client_id = job["client_id"]
        if client_id in seen_clients:
            exclusions["another_job_from_same_client"] += 1
            continue

        public = {
            key: job[key]
            for key in (
                "job_id",
                "title",
                "description",
                "skills",
                "screening_questions",
                "payment_type",
                "hourly_min",
                "hourly_max",
                "budget_min",
                "budget_max",
                "base_connects",
            )
        }
        hits = retrievers[persona].retrieve_job(public, k=1, filters={"type": "case_study"})
        if not hits:
            exclusions["no_grounded_case_study"] += 1
            continue
        limits = rates[persona]
        quote = price_job(
            public,
            minimum_project_budget=limits["min_project_usd"],
            minimum_hourly_rate=limits["floor_hourly_usd"],
            preferred_hourly_rate=limits["hourly_usd"],
            connects_budget=80,
        )
        if not quote.worth_it:
            exclusions["below_floor_or_unavailable_price"] += 1
            continue

        evidence = hits[0].evidence
        drafts, question = _draft_pair(public, evidence, include_case_details=include_case_details)
        if not all(
            _passes(draft, evidence, question, job["screening_questions"], policy)
            for draft in drafts
        ):
            exclusions["one_or_both_arms_failed_policy_or_evidence"] += 1
            continue

        seen_clients.add(client_id)
        decisions = [
            score_proposal(
                job=job,
                prefs=clients[client_id],
                cover_letter=draft.cover_letter,
                screening_answers=draft.screening_answers,
                bid_amount=quote.amount,
                proposal_id=f"x2-{job['job_id']}",
                seed=SEED,
                applicants=job["applicants"],
            )
            for draft in drafts
        ]
        evidence_result, question_result = decisions
        pairs.append(
            {
                "job_id": job["job_id"],
                "persona": persona,
                "client_id": client_id,
                "evidence_id": evidence.id,
                "bid_amount": quote.amount,
                "payment_type": quote.bid_type,
                "evidence_score": round(evidence_result.score, 4),
                "question_score": round(question_result.score, 4),
                "evidence_reply": evidence_result.outcome in {"replied", "interview"}
                and not evidence_result.ghost,
                "question_reply": question_result.outcome in {"replied", "interview"}
                and not question_result.ghost,
            }
        )

    return {
        "seed": SEED,
        "hypothesis": (
            "Evidence-first openings increase GigSim reply rate versus a scoped question."
        ),
        "simulator_mode": "frozen GigSim v1.0 decision rules; no marketplace submissions",
        "selection": "Good and good-vague synthetic dev jobs; one paired job per synthetic client.",
        "eligible_jobs": len(labels),
        "excluded_jobs": dict(sorted(exclusions.items())),
        "all": summarize(pairs),
        "by_persona": {
            name: summarize([row for row in pairs if row["persona"] == name])
            for name in personas
            if any(row["persona"] == name for row in pairs)
        },
        "paired_results": pairs,
    }


def _passes(draft, evidence: Evidence, question: str, screening: list[str], policy) -> bool:
    claims = verify_claims(draft.cover_letter, [evidence], planned_text=(question, PLAN))
    reports = [
        ClaimVerification(
            supported=item.status == "supported",
            claim=item.text,
            evidence_ids=list(item.evidence_ids),
        )
        for item in claims
    ]
    return check_proposal(draft, reports, screening, policy).allowed


def to_report(result: dict[str, Any]) -> dict[str, Any]:
    summary = result["all"]
    total = summary["pairs"]
    return {
        "schema_version": 1,
        "experiment": "X2 paired proposal openings",
        "scope": (
            "Frozen GigSim rules judged evidence-first and question-first openings on the same "
            "synthetic jobs, at most one job per client. The paired drafts shared the body, "
            "screening answers, quote and simulated randomness. No proposals were sent. "
            "The common body was revised after a zero-reply pilot, making the results exploratory. "
            "This experiment measures the rule-based simulator only."
        ),
        "command": COMMAND,
        "metrics": [
            {"name": "Comparable proposal pairs", "value": total},
            {
                "name": "Evidence-first replies",
                "value": summary["evidence_replies"],
                "denominator": total,
            },
            {
                "name": "Question-first replies",
                "value": summary["question_replies"],
                "denominator": total,
            },
            {
                "name": "Evidence-first reply-rate difference",
                "value": summary["reply_rate_difference_pp"],
                "unit": "percentage points",
            },
            {"name": "Exact two-sided McNemar p", "value": summary["exact_mcnemar_two_sided_p"]},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RESULTS)
    args = parser.parse_args()
    measured = evaluate()
    measured["pilot_without_explicit_case_details"] = evaluate(include_case_details=False)["all"]
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in (("x2.pairs.json", measured), ("x2.report.json", to_report(measured))):
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        (args.output / name).write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps(measured["all"], indent=2))


if __name__ == "__main__":
    main()
