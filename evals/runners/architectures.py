"""Compare three bid orchestration designs on the same 50 synthetic development jobs."""

import argparse
import asyncio
import json
import math
import re
from collections import Counter, defaultdict
from importlib.metadata import version
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.config import REPO_ROOT, get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.nodes.bid import OfflineBidNodes
from evals.runners.architecture_baselines import (
    CONTROLLER_MESSAGE,
    BaselineTools,
    pack,
    single_react,
    supervisor_reference,
    unpack,
)
from evals.runners.smoke import (
    GOLD_LABELS,
    SEED,
    PreviewMarketplace,
    public_jobs,
    read_jsonl,
    stratified_sample,
)
from gigsim.clients import score_proposal
from scripts.data.build_world import build

RESULTS = REPO_ROOT / "evals" / "results"
SIZE = 50
ARMS = ("single_react", "supervisor", "bid_graph")
LABELS = {
    "single_react": "Single deterministic ReAct controller",
    "supervisor": "LangGraph Supervisor with deterministic controller",
    "bid_graph": "Hand-built bid graph",
}
COMMAND = "uv run --extra eval python -m evals.runners.architectures"
REPOST = re.compile(r"\brepost(?:s|ed|ing)?\b", re.I)
WORD = re.compile(r"[a-z0-9]+", re.I)


def _world(sample: list[dict[str, Any]]):
    with TemporaryDirectory(prefix="gigbid-architectures-") as directory:
        root = Path(directory)
        build(seed=SEED, processed=root, dev_out=root / "dev.jsonl", test_out=root / "test.jsonl")
        rebuilt = {label["job_id"]: label for label in read_jsonl(root / "dev.jsonl")}
        for label in sample:
            fixture = rebuilt.get(label["job_id"])
            if not fixture or label["persona"] != fixture["persona"]:
                raise ValueError(f"Cannot match rebuilt job to label {label['job_id']}.")
            if label["label_source"] != "hand_verified" and label != fixture:
                raise ValueError(f"Construction label changed for {label['job_id']}.")
        world = root / "gigsim_world"
        public = public_jobs(world / "jobs.jsonl")
        jobs = {job["job_id"]: job for job in read_jsonl(world / "jobs.jsonl")}
        clients = {
            client["client_id"]: client["hidden_prefs"]
            for client in read_jsonl(world / "clients.jsonl")
        }
    return public, jobs, clients


def _summary(
    state: dict[str, Any],
    raw_job: dict[str, Any],
    preferences: dict[str, Any],
) -> dict[str, Any]:
    submission = state.get("submission")
    ready = state["status"] == "awaiting_approval" and submission is not None
    claims = state.get("claim_verification", [])
    reported = {
        "initial_action": state["decision"]["action"],
        "decision_reasons": state["decision"]["rule_ids"],
        "status": state["status"],
        "draft_reached_review": ready,
        "claim_count": len(claims),
        "claims_flagged_as_unsupported": sum(not claim["supported"] for claim in claims),
        "compliance_block": state.get("compliance_verdict", {}).get("allowed") is False,
        "risk_flags": state.get("risk_report", {}).get("flags", []),
        "evidence_ids": state.get("proposal_draft", {}).get("evidence_ids", []),
        "errors": state.get("errors", []),
        "model_tokens": 0,
        "simulated_reply": None,
    }
    if ready:
        decision = score_proposal(
            job=raw_job,
            prefs=preferences,
            cover_letter=submission["cover_letter"],
            screening_answers=submission["screening_answers"],
            bid_amount=submission["bid_amount"],
            proposal_id=f"architecture-{raw_job['job_id']}",
            seed=SEED,
            applicants=raw_job["applicants"],
        )
        reported.update(
            simulated_reply=decision.outcome in {"replied", "interview"} and not decision.ghost,
            simulated_score=round(decision.score, 4),
            simulated_reasons=decision.reasons,
        )
    return reported


def _p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def repost_signals(raw_jobs: dict[str, dict[str, Any]]) -> set[str]:
    """Find explicit reposts corroborated by a similar public listing from the same client."""
    by_client: dict[str, list[tuple[str, set[str]]]] = defaultdict(list)
    hinted: list[tuple[str, str, set[str]]] = []
    for eid, record in raw_jobs.items():
        if record.get("status", "open") != "open":
            continue
        text = f"{record['title']} {record['description']}"
        words = set(WORD.findall(text.casefold()))
        client = record["client_id"]
        by_client[client].append((eid, words))
        if REPOST.search(text):
            hinted.append((eid, client, words))

    return {
        eid
        for eid, client, words in hinted
        if any(
            candidate != eid and len(words & other) / max(1, len(words | other)) >= 0.80
            for candidate, other in by_client[client]
        )
    }


def summarize(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    selected = [row["arms"][arm] for row in rows]
    expected = [row["expected_bid"] for row in rows]
    predicted = [result["initial_action"] == "bid" for result in selected]
    true = sum(guess and label for guess, label in zip(predicted, expected, strict=True))
    false_positive = sum(
        guess and not label for guess, label in zip(predicted, expected, strict=True)
    )
    false_negative = sum(
        label and not guess for guess, label in zip(predicted, expected, strict=True)
    )
    f1_denominator = 2 * true + false_positive + false_negative
    latency = [item["orchestration_ms"] for item in selected]
    return {
        "jobs": len(rows),
        "correct_initial_decisions": sum(
            guess == label for guess, label in zip(predicted, expected, strict=True)
        ),
        "bid_f1": round(2 * true / f1_denominator, 4) if f1_denominator else 0,
        "drafts_reaching_review": sum(item["draft_reached_review"] for item in selected),
        "claims_checked": sum(item["claim_count"] for item in selected),
        "claims_flagged_as_unsupported": sum(
            item["claims_flagged_as_unsupported"] for item in selected
        ),
        "compliance_blocks": sum(item["compliance_block"] for item in selected),
        "simulated_replies": sum(item["simulated_reply"] is True for item in selected),
        "controller_turns": sum(item["controller_turns"] for item in selected),
        "model_tokens": 0,
        "measured_p50_orchestration_ms": round(median(latency), 3),
        "measured_p95_orchestration_ms": round(_p95(latency), 3),
    }


def failures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for row in rows:
        outcome = row["arms"]["bid_graph"]
        actual_bid = outcome["initial_action"] == "bid"
        expected_bid = row["expected_bid"]
        category = None
        if actual_bid != expected_bid:
            if row["kind"] == "duplicate":
                category = "duplicate signal unavailable"
            elif row["kind"] == "below_floor":
                category = "below-floor job passed the initial decision gate"
            elif row["kind"] == "scam":
                category = "scam passed the initial decision gate"
            else:
                category = "wrong initial bid" if actual_bid else "wrong initial skip or watch"
        elif row["has_injection"] and "prompt_injection" not in outcome["risk_flags"]:
            category = "planted injection not flagged"
        elif expected_bid and not outcome["draft_reached_review"]:
            category = "expected bid did not reach approval"
        elif expected_bid and outcome["simulated_reply"] is False:
            category = "simulated client did not reply"
        if category is not None:
            cases.append(
                {
                    "job_id": row["job_id"],
                    "persona": row["persona"],
                    "kind": row["kind"],
                    "category": category,
                    "expected_bid": expected_bid,
                    "initial_action": outcome["initial_action"],
                    "status": outcome["status"],
                    "decision_reasons": outcome["decision_reasons"],
                    "risk_flags": outcome["risk_flags"],
                    "errors": outcome["errors"],
                    "simulator_reasons": outcome.get("simulated_reasons", []),
                    "replay": row.get("replay"),
                }
            )
    return cases


async def compare(*, size: int = SIZE) -> dict[str, Any]:
    sample = stratified_sample(read_jsonl(GOLD_LABELS), size)
    public, raw_jobs, clients = _world(sample)
    settings = get_config()
    balance = int(settings.connects["default_weekly_budget"])
    reposts = repost_signals(raw_jobs)
    workflows = {}
    services = {}
    for persona in sorted({row["persona"] for row in sample}):
        nodes = OfflineBidNodes.from_persona(persona, config=settings)
        services[persona] = nodes
        marketplace = PreviewMarketplace(public, persona, balance)
        tools = BaselineTools(nodes, marketplace, settings)
        workflows[persona] = {
            "single_react": single_react(tools),
            "supervisor": supervisor_reference(tools),
            "bid_graph": build_bid_graph(
                nodes=nodes,
                marketplace=marketplace,
                checkpointer=InMemorySaver(),
                config=settings,
            ),
        }

    rows = []
    for index, label in enumerate(sample):
        job_id, persona = label["job_id"], label["persona"]
        original = raw_jobs[job_id]
        config = {"configurable": {"thread_id": f"part-c:{persona}:{job_id}"}}
        result = {}
        # Rotate arm order to avoid always timing one workflow cold or last.
        order = ARMS[index % len(ARMS) :] + ARMS[: index % len(ARMS)]
        for arm in order:
            workflow = workflows[persona][arm]
            if arm == "bid_graph":
                inputs = {"job": public[job_id]}
            else:
                payload = {"job": public[job_id], "stage": "assess"}
                inputs = {"messages": [HumanMessage(content=pack(payload))]}
            start = perf_counter_ns()
            output = await workflow.ainvoke(inputs, config=config)
            latency_ms = (perf_counter_ns() - start) / 1_000_000
            messages = output.get("messages", [])
            state = output if arm == "bid_graph" else unpack(messages)
            if (
                arm == "bid_graph"
                and state["status"] == "awaiting_approval"
                and not output.get("__interrupt__")
            ):
                raise RuntimeError(
                    "Bid graph returned a review draft without an approval interrupt."
                )
            evaluated = _summary(state, original, clients[original["client_id"]])
            evaluated.update(
                orchestration_ms=round(latency_ms, 4),
                controller_turns=sum(
                    message.response_metadata.get(CONTROLLER_MESSAGE) is True
                    for message in messages
                ),
            )
            result[arm] = evaluated
        rows.append(
            {
                "job_id": job_id,
                "persona": persona,
                "kind": label["kind"],
                "expected_bid": label["bid_or_skip"] == "bid",
                "has_injection": label["has_injection"],
                "arms": {arm: result[arm] for arm in ARMS},
            }
        )

    for row in rows:
        job = public[row["job_id"]]
        persona = row["persona"]
        service = services[persona]
        floor = (
            service.minimum_hourly_rate
            if job["payment_type"] == "hourly"
            else service.minimum_project_budget
        )
        inputs = {"job": job, "price_floor": floor, "duplicate": row["job_id"] in reposts}
        thread = {"configurable": {"thread_id": f"part-c-replay:{persona}:{row['job_id']}"}}
        state = await workflows[persona]["bid_graph"].ainvoke(inputs, config=thread)
        replay_bid = state["decision"]["action"] == "bid"
        row["replay"] = {
            "price_floor": floor,
            "public_repost_signal": inputs["duplicate"],
            "initial_action": state["decision"]["action"],
            "decision_reasons": state["decision"]["rule_ids"],
            "status": state["status"],
            "correct_initial_decision": replay_bid == row["expected_bid"],
        }

    grouped = failures(rows)
    return {
        "dataset": f"{size} synthetic dev jobs stratified by label kind and persona",
        "seed": SEED,
        "supervisor_package_version": version("langgraph-supervisor"),
        "single_baseline": "LangGraph prebuilt ReAct with a deterministic tool-calling script",
        "supervisor_baseline": (
            "Actual langgraph-supervisor package using the same scripted controller"
        ),
        "common_tools": (
            "Shared production analysis, writer, claim checks, critic and compliance rules"
        ),
        "timing": (
            "Local wall time for orchestration through review; excludes setup and reply scoring"
        ),
        "summary": {arm: summarize(rows, arm) for arm in ARMS},
        "bid_graph_failure_counts": dict(
            sorted(Counter(row["category"] for row in grouped).items())
        ),
        "bid_graph_failures": grouped,
        "diagnostic_replay": {
            "scope": "Existing persona price floor and corroborated public repost hints only",
            "correct_initial_decisions": sum(
                row["replay"]["correct_initial_decision"] for row in rows
            ),
            "jobs": len(rows),
        },
        "paired_job_results": rows,
    }


def to_report(result: dict[str, Any]) -> dict[str, Any]:
    metrics = []
    for arm in ARMS:
        scores = result["summary"][arm]
        name = LABELS[arm]
        metrics.extend(
            (
                {
                    "name": f"{name} correct initial decisions",
                    "value": scores["correct_initial_decisions"],
                    "denominator": scores["jobs"],
                },
                {"name": f"{name} bid F1", "value": scores["bid_f1"]},
                {
                    "name": f"{name} drafts reaching review",
                    "value": scores["drafts_reaching_review"],
                    "denominator": scores["jobs"],
                },
                {"name": f"{name} simulated replies", "value": scores["simulated_replies"]},
                {
                    "name": f"{name} unsupported claims flagged",
                    "value": scores["claims_flagged_as_unsupported"],
                },
                {"name": f"{name} compliance blocks", "value": scores["compliance_blocks"]},
            )
        )
    return {
        "schema_version": 1,
        "experiment": "Part C · Three orchestration architectures",
        "scope": (
            f"{result['dataset']}. All arms share deterministic analysis, writer and policy tools. "
            "The prebuilt single-agent ReAct and the installed LangGraph Supervisor reference use "
            "scripted tool calls. No model inference, tokens, clients contacted or proposals sent. "
            "Local timings and per-job failure annotations are in architecture.dev.json."
        ),
        "command": COMMAND,
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RESULTS)
    args = parser.parse_args()
    result = asyncio.run(compare())
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in (
        ("architecture.dev.json", result),
        ("architecture.report.json", to_report(result)),
    ):
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        (args.output / name).write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
