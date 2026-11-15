"""Prepare blind X2 proposal pairs for classmates and summarize completed ballots."""

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent.config import REPO_ROOT
from evals.runners.x2 import PERSONAS, RESULTS, _draft_pair, _world, exact_mcnemar
from rag.corpus import load_corpus

DEFAULT_OUTPUT = REPO_ROOT / ".cache" / "human-panel"
PERSONA_NAMES = ("ai_ml", "data_analyst", "fullstack")
SEED = 4016
PAIRS_PER_PERSONA = 4
_CITATIONS = re.compile(r"\s*\[evidence:[^\]]+\]")
Choice = Literal["A", "B", "tie"]
Strategy = Literal["evidence_first", "question_first"]


class BallotItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pair_id: str
    choice: Choice | None


def _anon(body: str) -> str:
    return _CITATIONS.sub("", body)


def prepare(directory: Path) -> None:
    """Draw 12 recorded eligible pairs with balanced positions within each persona."""
    if (directory / "strategy-key.json").exists():
        raise ValueError("Choose a new directory to prepare another panel packet.")
    x2 = json.loads((RESULTS / "x2.pairs.json").read_text(encoding="utf-8"))
    jobs, _, _ = _world()
    chooser = random.Random(SEED)
    packet = []
    for persona in PERSONA_NAMES:
        candidates = [row for row in x2["paired_results"] if row["persona"] == persona]
        selected = chooser.sample(candidates, PAIRS_PER_PERSONA)
        documents = {item.id: item for item in load_corpus(PERSONAS / persona)}
        orders: list[Strategy] = ["evidence_first", "question_first"] * (PAIRS_PER_PERSONA // 2)
        chooser.shuffle(orders)
        for row, first in zip(selected, orders, strict=True):
            public = jobs[row["job_id"]]
            evidence = documents[row["evidence_id"]]
            drafts, _ = _draft_pair(public, evidence, include_case_details=True)
            proposals = tuple(_anon(draft.cover_letter) for draft in drafts)
            if first == "question_first":
                proposals = proposals[::-1]
            job = f"{public['title']}\n\n{public['description']}"
            combined = "\0".join([job, *proposals])
            pair_id = "pair-" + hashlib.sha256(combined.encode()).hexdigest()[:12]
            packet.append(
                {
                    "pair_id": pair_id,
                    "first": first,
                    "job": job,
                    "evidence": evidence.text,
                    "proposals": proposals,
                }
            )
    chooser.shuffle(packet)
    directions = [
        "# Blind proposal comparison",
        "",
        "For each job, compare proposals A and B on relevance to the requested work, clarity,",
        "and whether the portfolio supports the proposal's claims. The case and portfolio are",
        "synthetic. Choose `A` or `B`, or use `tie` if there is no meaningful preference.",
        "Work independently. Replace each `null` in a copy of `blank-ballot.jsonl` and return it.",
        "",
    ]
    for pair in packet:
        directions.extend(
            [
                f"## {pair['pair_id']}",
                "",
                "### Job",
                "",
                pair["job"],
                "",
                "### Portfolio evidence",
                "",
                pair["evidence"],
                "",
                "### Proposal A",
                "",
                pair["proposals"][0],
                "",
                "### Proposal B",
                "",
                pair["proposals"][1],
                "",
            ]
        )
    share = directory / "reviewer-files"
    share.mkdir(parents=True, exist_ok=True)
    (share / "pairwise-review-packet.md").write_text("\n".join(directions), encoding="utf-8")
    blank = [BallotItem(pair_id=pair["pair_id"], choice=None).model_dump_json() for pair in packet]
    (share / "blank-ballot.jsonl").write_text("\n".join(blank) + "\n", encoding="utf-8")
    key = {pair["pair_id"]: pair["first"] for pair in packet}
    (directory / "strategy-key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")


def _read_ballot(path: Path, pair_ids: set[str]) -> dict[str, Choice]:
    ballot: dict[str, Choice] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        vote = BallotItem.model_validate_json(line)
        if vote.pair_id in ballot or vote.pair_id not in pair_ids or vote.choice is None:
            raise ValueError(f"Duplicate, unknown or missing choice for {vote.pair_id} in {path}.")
        ballot[vote.pair_id] = vote.choice
    if pair_ids != ballot.keys():
        raise ValueError(f"All pairs need a valid choice in {path}.")
    return ballot


def summarize(key: Mapping[str, Strategy], ballots: list[dict[str, Choice]]) -> dict:
    majority = Counter()
    by_vote = Counter()
    for pair_id, first in key.items():
        second = "question_first" if first == "evidence_first" else "evidence_first"
        choices = {"A": first, "B": second, "tie": "tie"}
        counts = Counter(choices[ballot[pair_id]] for ballot in ballots)
        by_vote.update(counts)
        winner, votes = counts.most_common(1)[0]
        majority[winner if votes > len(ballots) / 2 else "tie"] += 1
    outcomes = ("evidence_first", "question_first", "tie")
    decisive = majority["evidence_first"] + majority["question_first"]
    return {
        "reviewer_files": len(ballots),
        "pairs": len(key),
        "vote_counts": {outcome: by_vote[outcome] for outcome in outcomes},
        "pair_majorities": {outcome: majority[outcome] for outcome in outcomes},
        "exact_two_sided_sign_test_p": (
            exact_mcnemar(majority["evidence_first"], majority["question_first"])
            if decisive
            else None
        ),
        "scope": "Descriptive summary of submitted ballots on 12 synthetic dev pairs.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_OUTPUT)
    modes = parser.add_subparsers(dest="command", required=True)
    modes.add_parser("prepare")
    score = modes.add_parser("score")
    score.add_argument("ballots", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.directory)
            print(args.directory / "reviewer-files")
            return
        paths = [path.resolve(strict=True) for path in args.ballots]
        if len(set(paths)) != len(paths) or len(paths) < 2:
            raise ValueError("Provide one ballot from each of at least two reviewers.")
        key = json.loads((args.directory / "strategy-key.json").read_text(encoding="utf-8"))
        if len(key) != PAIRS_PER_PERSONA * len(PERSONA_NAMES) or set(key.values()) != {
            "question_first",
            "evidence_first",
        }:
            raise ValueError("The strategy key does not describe the prepared panel.")
        result = summarize(key, [_read_ballot(path, set(key)) for path in paths])
        payload = json.dumps(result, indent=2) + "\n"
        (args.directory / "panel-result.json").write_text(payload, encoding="utf-8")
        print(payload, end="")
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
