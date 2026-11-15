"""Hand-verify gold labels: shows a job and its gold label, you agree or correct it.

    uv run python -m scripts.data.label_tool review --n 60      # every scam and injection +
                                                                # a stratified sample of the rest
    uv run python -m scripts.data.label_tool summary            # agreement by kind

Answers are appended to evals/datasets/label_review.jsonl, so a session can stop and resume.
The agreement rate goes into docs/DATA_CARD.md. Labels are never edited after seeing model
results; a correction here is recorded with its reason instead.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from scripts.data.sources import PROCESSED, ROOT

DEV = ROOT / "evals" / "datasets" / "dev.jsonl"
REVIEW = ROOT / "evals" / "datasets" / "label_review.jsonl"
WORLD_JOBS = PROCESSED / "gigsim_world" / "jobs.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def review_sample(gold: list[dict], n_other: int, seed: int = 7) -> list[dict]:
    must = [g for g in gold if g["is_scam"] or g["has_injection"] or g["read_check"]]
    rest = [g for g in gold if g not in must]
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for g in rest:
        by_kind[g["kind"]].append(g)
    r = random.Random(seed)
    picked: list[dict] = []
    for kind in sorted(by_kind):
        share = max(1, round(n_other * len(by_kind[kind]) / max(1, len(rest))))
        picked += r.sample(by_kind[kind], min(share, len(by_kind[kind])))
    return must + picked


def summarise(reviews: list[dict]) -> dict:
    by_kind: dict[str, Counter] = defaultdict(Counter)
    for row in reviews:
        by_kind[row["kind"]]["agree" if row["agree"] else "disagree"] += 1
    total = sum(c["agree"] + c["disagree"] for c in by_kind.values())
    agree = sum(c["agree"] for c in by_kind.values())
    return {"reviewed": total, "agreement": round(agree / total, 3) if total else None,
            "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())}}


def review(n_other: int) -> None:
    gold = load_jsonl(DEV)
    jobs = {j["job_id"]: j for j in load_jsonl(WORLD_JOBS)}
    done = {r["job_id"] for r in load_jsonl(REVIEW)}
    todo = [g for g in review_sample(gold, n_other) if g["job_id"] not in done]
    print(f"{len(todo)} jobs to review (Enter = agree, d = disagree, q = quit)\n")
    with REVIEW.open("a", encoding="utf-8") as fh:
        for i, g in enumerate(todo, 1):
            job = jobs.get(g["job_id"], {})
            print("=" * 80)
            print(f"[{i}/{len(todo)}] persona={g['persona']} kind={g['kind']}")
            print(job.get("title", "?"))
            print(job.get("description", "?")[:1500])
            print("Screening:", job.get("screening_questions"))
            print(f"Budget: {job.get('budget_max') or job.get('hourly_max')} "
                  f"({job.get('payment_type')})")
            print(f"GOLD -> {g['bid_or_skip']} ({g['skip_reason']}); scam={g['is_scam']} "
                  f"injection={g['has_injection']} evidence={[e['id'] for e in g['gold_evidence']]}")
            answer = input("agree? ").strip().lower()
            if answer == "q":
                break
            row = {"job_id": g["job_id"], "kind": g["kind"], "agree": answer != "d"}
            if answer == "d":
                row["note"] = input("what is wrong? ").strip()
            fh.write(json.dumps(row) + "\n")
    print(json.dumps(summarise(load_jsonl(REVIEW)), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    rv = sub.add_parser("review")
    rv.add_argument("--n", type=int, default=60, help="extra jobs beyond scams and injections")
    sub.add_parser("summary")
    args = parser.parse_args()
    if args.cmd == "review":
        review(args.n)
    else:
        print(json.dumps(summarise(load_jsonl(REVIEW)), indent=2))


if __name__ == "__main__":
    main()
