# Evaluation results

## Recorded runs

<!-- BEGIN EVALUATION RESULTS -->

### Offline bid graph smoke

30 synthetic development jobs sampled by persona and job kind with seed 4016. Initial decisions are scored before drafting and watch counts as a non-bid. The graph stops at human approval. Claims are checked by the offline verifier and no proposals are sent.

| Metric | Result |
| --- | ---: |
| Correct bid decisions | 21 / 30 |
| Bid F1 | 0.7097 |
| Drafts reaching review | 14 / 30 |
| Claims flagged as unsupported at review | 0 / 28 |

[Result artifact](../evals/results/bid-smoke.report.json)

Reproduce with <code>uv run python -m evals.runners.smoke --jobs 30 --gate</code>.

<!-- END EVALUATION RESULTS -->

## Pending studies

Model-profile comparison, retrieval variants and ablation, architecture comparison, tool
granularity, proposal experiments, and a held-out deployment run still need measurements.

## Regenerate the tables

Store each result as `evals/results/<name>.report.json` with a schema version, experiment name,
scope, reproduction command, and named metric values. Diagnostic outputs with other filenames
stay out of the report. Run `uv run python -m scripts.make_tables` after any change to those
artifacts. CI uses `--check` to detect stale tables and invalid artifacts.
