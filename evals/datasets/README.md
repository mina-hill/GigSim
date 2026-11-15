# Evaluation datasets

`dev.jsonl` holds gold labels for the 600 development jobs (200 per persona). The job texts live in GigSim's world (`data/processed/gigsim_world/jobs.jsonl`, rebuilt with `make world`); join on `job_id`. The held-out test labels are never committed: they are written to `$GIGBID_TEST_SPLIT_PATH`.

| Field | Meaning |
| --- | --- |
| `job_id` | GigSim job id (opaque, reveals nothing about the labels) |
| `persona`, `account_id` | The freelancer this label is for |
| `kind` | How the job was made: good, good_vague, wrong_domain, off_domain, below_floor, scam, injection, read_check, duplicate |
| `bid_or_skip` | Gold decision |
| `skip_reason` | skills_do_not_match, outside_my_domain, budget_below_floor, scam, duplicate_posting |
| `is_scam`, `has_injection`, `read_check`, `duplicate_of` | Risk labels |
| `gold_evidence` | Portfolio documents that prove the requirements, graded 2 (proves it) or 1 (related) |
| `fair_price_band` | [low, high] from market statistics for the job's domain, level and payment type, scaled by scope |
| `tags` | Breakdown dimensions: number of requirements, vague or specific, exact library names, multi-project, injection type, scam type |
| `label_source` | `construction` (known from how the job was built), `domain_rule` (real Kaggle post outside the persona's domain), `hand_verified` |

Labels from construction are only as good as the generator; `scripts/data/label_tool.py` is used to hand-check a stratified sample and the agreement rate is reported in `docs/DATA_CARD.md`.
