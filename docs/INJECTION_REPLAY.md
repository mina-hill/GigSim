# Local prompt-injection replay

The evaluation rebuilds the synthetic GigSim development set and runs all 42 committed
planted-injection jobs through the bid graph. Each job is paired with the same job after
removing the plant. The conversation graph separately receives each directive after a benign
client question and receives the same question on its own. The market doubles permit previews
and record any attempted write. The runner never grants human approval.

| Measure | Bid graph | Conversation graph |
| --- | ---: | ---: |
| Planted cases flagged | 42/42 | 42/42 |
| Matched clean controls flagged | 0/42 | 0/42 |
| Clean controls reaching human review | 33/42 | 42/42 |
| Planted cases reaching human review | 33/42 | 0/42 |
| Planted cases carrying the complete directive into draft text or attempting a write | 0/42 | 0/42 |
| Unapproved writes attempted | 0/42 | 0/42 |

Removing the plant left the bid decision, fit and price unchanged for all 42 listings.
An eligible job can still reach proposal review after a prompt-injection flag. The
conversation graph disengages from every planted client directive in this set.

The direct-copy positive control carries 42/42 directives into text by construction. It
verifies that the measurement detects copying. It does not estimate success against an
undefended LLM. The graph run measures exact normalized full-directive copying and write
attempts, with a separate paired check of bid decision, fit and price. These fixtures do not
cover every paraphrase or attack route. Model inference and live marketplace calls were
outside this run.

```bash
uv run python -m evals.runners.injection
uv run pytest -q tests/integration/test_injection_replay.py
```

[Case outcomes](../evals/results/injection.dev.json) · [Report metrics](../evals/results/injection.report.json)
