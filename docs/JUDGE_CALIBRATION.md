# Blind grounding calibration

The calibration packet contains 30 anonymized claims built from ten synthetic GigBid portfolio
excerpts. The seeded order mixes faithfully grounded claims with added or entirely unrelated
facts. Reviewers get the claim, an evidence excerpt and the rubric. The packet has no
construction keys or automated ratings.

```bash
uv run python -m evals.runners.judge_calibration prepare
```

Open `.cache/judge-calibration/reviewer-packet.md` and rate all 30 items in
`reviewer-labels.jsonl` beside it before looking at automated ratings. Use `supported` when
all factual parts follow, `partly_supported` when some follow but material parts do not,
or `unsupported` when the central claim lacks evidence or is contradicted. Running
`prepare` again preserves the existing review labels.

Run the calibrated model locally if Ollama and the judge model specified in
`agent/config.yaml` are available. In PowerShell set `$env:MODEL_PROFILE = 'local'`;
in Bash use `export MODEL_PROFILE=local`.

```bash
uv run python -m evals.runners.judge_calibration judge --backend ollama
uv run python -m evals.runners.judge_calibration score
```

The judge command accepts only a configured Ollama endpoint on loopback and validates
each response. Use `--backend verifier` for a deterministic workflow check. The verifier
backend is reported separately from an LLM judge.

The score command requires exactly one valid rating for every item on both sides. It
reports raw agreement, Cohen's kappa and the 3-by-3 confusion matrix in
`calibration-result.json`. Kappa is reported as null if chance adjustment is undefined.
The sample reuses ten excerpts and focuses on factual grounding, so a score does not
generalize to broad proposal quality or independent human preference. No independently
completed ratings or measured LLM kappa are included in the repository.

## Blind pairwise panel

The panel draws four completed pairs per persona from the checked-in X2 synthetic
development run. Within each persona, the two writing strategies appear equally often
in position A. The shared packet removes strategy labels and internal citations and
includes the job, portfolio evidence and both proposal texts.

```bash
uv run --extra rag python -m evals.runners.human_panel prepare
```

Share only the two files in `.cache/human-panel/reviewer-files` with classmates.
Keep `strategy-key.json` local until the ballots are collected. Each reviewer completes
a separate copy of the blank ballot with `A`, `B` or `tie` for each pair.

```bash
uv run --extra rag python -m evals.runners.human_panel score first-reviewer.jsonl second-reviewer.jsonl
```

The scoring command validates the ballots and decodes A/B positions. It reports vote
totals, strict majority per job and an exact two-sided sign test across decisive jobs.
The small synthetic sample supports a descriptive preference check. No classroom votes
are included.
