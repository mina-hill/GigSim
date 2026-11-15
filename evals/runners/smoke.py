"""Evaluate the offline bid graph on a fixed, stratified synthetic development sample."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import defaultdict
from itertools import zip_longest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from agent.config import REPO_ROOT, get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.marketplace_gateway import BidPreview
from agent.nodes.bid import OfflineBidNodes
from mcp_servers.marketplace.port import Job, Proposal, ProposalInput
from scripts.data.build_world import build

REPORT = REPO_ROOT / "evals" / "results" / "bid-smoke.report.json"
GOLD_LABELS = REPO_ROOT / "evals" / "datasets" / "dev.jsonl"
SEED = 4016
SAMPLE_SIZE = 30


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def stratified_sample(labels: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    if not 1 <= size <= len(labels):
        raise ValueError(f"Choose between 1 and {len(labels)} jobs.")
    strata = defaultdict(list)
    for row in sorted(labels, key=lambda entry: entry["job_id"]):
        strata[row["kind"], row["persona"]].append(row)
    rng = random.Random(SEED)
    for group in strata.values():
        rng.shuffle(group)
    return [
        row
        for layer in zip_longest(*(strata[key] for key in sorted(strata)))
        for row in layer
        if row is not None
    ][:size]


class PreviewMarketplace:
    """Provide reproducible Connects quotes without performing a marketplace write."""

    def __init__(self, jobs: dict[str, dict[str, Any]], persona: str, balance: int) -> None:
        self.jobs = jobs
        self.account_id = f"persona_{persona}"
        self.balance = balance

    async def get_job(self, job_id: str) -> Job:
        raise RuntimeError(f"Smoke evaluation already supplied the fixture for {job_id}.")

    async def preview(self, proposal: ProposalInput) -> BidPreview:
        cost = self.jobs[proposal.job_id]["connects_cost"]
        return BidPreview(
            job_id=proposal.job_id,
            connects_cost=cost,
            multipliers_applied=[],
            balance=self.balance,
            balance_after=self.balance - cost,
            policy_version="v1",
            account_id=self.account_id,
        )

    async def submit(
        self, proposal: ProposalInput, *, idempotency_key: str, approved_quote: BidPreview
    ) -> Proposal:
        raise RuntimeError(f"An unapproved smoke run tried to submit {proposal.job_id}.")


async def evaluate(sample: list[dict[str, Any]], jobs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    settings = get_config()
    balance = int(settings.connects["default_weekly_budget"])
    graphs = {
        persona: build_bid_graph(
            nodes=OfflineBidNodes.from_persona(persona, config=settings),
            marketplace=PreviewMarketplace(jobs, persona, balance),
            checkpointer=InMemorySaver(),
            config=settings,
        )
        for persona in sorted({row["persona"] for row in sample})
    }
    correct = true_positive = false_positive = false_negative = 0
    previews = claims = unsupported = 0
    for row in sample:
        job_id, persona = row["job_id"], row["persona"]
        state = await graphs[persona].ainvoke(
            {"job": jobs[job_id]}, {"configurable": {"thread_id": f"{persona}:{job_id}"}}
        )
        predicted_bid = state["decision"]["action"] == "bid"
        expected_bid = row["bid_or_skip"] == "bid"
        correct += predicted_bid == expected_bid
        true_positive += predicted_bid and expected_bid
        false_positive += predicted_bid and not expected_bid
        false_negative += expected_bid and not predicted_bid
        if state.get("__interrupt__"):
            previews += 1
            claims += len(state["claim_verification"])
            unsupported += sum(not claim["supported"] for claim in state["claim_verification"])
    f1_denominator = 2 * true_positive + false_positive + false_negative
    f1 = (2 * true_positive / f1_denominator) if f1_denominator else 0.0
    claim_metric = {"name": "Claims flagged as unsupported at review", "value": unsupported}
    if claims:
        claim_metric["denominator"] = claims
    command = f"uv run python -m evals.runners.smoke --jobs {len(sample)}"
    if len(sample) == SAMPLE_SIZE:
        command += " --gate"
    else:
        command += " --output /path/to/result.report.json"
    return {
        "schema_version": 1,
        "experiment": "Offline bid graph smoke",
        "scope": (
            f"{len(sample)} synthetic development jobs sampled by persona and job kind with "
            f"seed {SEED}. Initial decisions are scored before drafting and watch counts as "
            "a non-bid. The graph stops at human approval. Claims are checked by the offline "
            "verifier and no proposals are sent."
        ),
        "command": command,
        "metrics": [
            {"name": "Correct bid decisions", "value": correct, "denominator": len(sample)},
            {"name": "Bid F1", "value": round(f1, 4)},
            {"name": "Drafts reaching review", "value": previews, "denominator": len(sample)},
            claim_metric,
        ],
    }


def public_jobs(path: Path) -> dict[str, dict[str, Any]]:
    fields = Job.model_fields.keys() - {"client"}
    return {
        raw["job_id"]: {
            **{name: raw[name] for name in fields if name in raw},
            "connects_cost": raw.get("base_connects", 8),
        }
        for raw in read_jsonl(path)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or REPORT
    if args.jobs != SAMPLE_SIZE and (args.gate or output.resolve() == REPORT):
        parser.error("Nonstandard sample sizes require a separate --output and omit --gate.")
    try:
        sample = stratified_sample(read_jsonl(GOLD_LABELS), args.jobs)
    except ValueError as error:
        parser.error(str(error))
    with TemporaryDirectory(prefix="gigbid-smoke-") as directory:
        temp = Path(directory)
        build(
            seed=SEED,
            processed=temp,
            dev_out=temp / "dev.jsonl",
            test_out=temp / "test.jsonl",
        )
        rebuilt = {row["job_id"]: row for row in read_jsonl(temp / "dev.jsonl")}
        for row in sample:
            current = rebuilt.get(row["job_id"])
            if not current or row["persona"] != current["persona"]:
                raise SystemExit(f"Cannot match committed label to rebuilt job {row['job_id']}.")
            if row["label_source"] != "hand_verified" and row != current:
                raise SystemExit(f"Rebuilt construction label changed for {row['job_id']}.")
        jobs = public_jobs(temp / "gigsim_world" / "jobs.jsonl")
        measured = asyncio.run(evaluate(sample, jobs))

    output.parent.mkdir(parents=True, exist_ok=True)
    report = json.dumps(measured, indent=2, ensure_ascii=False) + "\n"
    output.write_text(report, encoding="utf-8", newline="\n")
    print(report, end="")
    _, bid_f1, previews, unsupported = measured["metrics"]
    if args.gate and (bid_f1["value"] < 0.60 or not previews["value"] or unsupported["value"]):
        raise SystemExit("Smoke gate failed. Review the recorded metrics.")


if __name__ == "__main__":
    main()
