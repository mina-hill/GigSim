# Data card

GigBid uses two kinds of data: **real job-market data** from public Kaggle datasets, and **synthetic data** we generate (freelancer personas, client preferences, proposals, conversations, most evaluation jobs). Every record carries a `provenance` field (`kaggle:<dataset>` or `synthetic:<generator>@<version>`) so the two never mix silently.

## Sources and licences

| Dataset | Licence | What we use it for |
| --- | --- | --- |
| [Upwork Freelance Jobs 60K](https://www.kaggle.com/datasets/ahmedmyalo/upwork-freelance-jobs-60k) (ahmedmyalo) | Apache 2.0 | Historical job texts, skill tags, client statistics, off-domain jobs, price statistics |
| [Upwork Job Postings 2024, 50K](https://www.kaggle.com/datasets/asaniczka/upwork-job-postings-dataset-2024-50k-records) (asaniczka) | ODC-By | Posted budgets and hourly ranges for market statistics |
| [All Jobs on Upwork, 200K+](https://www.kaggle.com/datasets/asaniczka/all-jobs-on-upwork-200k-plus) (asaniczka) | ODC-By | More price observations (deduplicated against the 50K set) |
| [Freelance Contracts, 1.3M](https://www.kaggle.com/datasets/asaniczka/freelance-contracts-dataset-1-3-million-entries) (asaniczka) | ODC-By | Realised hourly rates |
| [Freelancer Success, Earnings and Churn](https://www.kaggle.com/datasets/sarveshchhetri/freelancer-success-earnings-and-churn-prediction) (sarveshchhetri) | Apache 2.0; itself synthetic | Realistic freelancer rate ranges |
| [trial-1 / Fake Job Posting Detection](https://www.kaggle.com/datasets/adityammantri/trial-1) (adityammantri) | Not stated | Wording of fraudulent postings, used to vary generated scam jobs. Internal use only; not redistributed |

**Attribution (ODC-By).** Contains information from the asaniczka Upwork datasets listed above, made available under the Open Data Commons Attribution License.

**Collection and scraping.** The Upwork datasets were collected by their Kaggle authors. GigBid scrapes nothing: we drop every URL column and never follow a link from these files. Live mode uses Upwork's official MCP server only.

**Not in the repository.** Raw and processed Kaggle files live in `data/raw/` and `data/processed/` (git-ignored); `scripts/data/download.py` records file hashes in `data/raw/download_manifest.json`.

## Preprocessing (`scripts/data/preprocess.py`)

1. Map each file's columns to logical fields (`scripts/data/sources.py`; `--inspect` prints the mapping).
2. Clean text: strip HTML; mask URLs, emails and phone numbers; normalise whitespace.
3. Keep English postings (ASCII letters plus a share of English function words).
4. Remove exact duplicates (normalised title + first 500 characters), then near duplicates (MinHash LSH on word 5-shingles, Jaccard ≥ 0.9).
5. Tag a domain per posting (AI/ML, full-stack web, data analysis, other) by keyword vote, with the title counted double.
6. Build market statistics: P25, P50, P75 of fixed budgets and hourly rates per domain × experience level × payment type (plus an "any level" aggregate), after removing out-of-range values (fixed 5–100,000 USD; hourly 3–300 USD); groups need at least 20 observations.

Row counts after each step are written to `data/processed/preprocess_report.json` and copied here after each run.

| Step | Rows |
| --- | --- |
| (fill in from preprocess_report.json after the Kaggle run) | |

## Synthetic data

| Data | Generator | Notes |
| --- | --- | --- |
| Three freelancer personas (AI/ML engineer, full-stack developer, data analyst) | Hand-written bibles in `data/personas/*/bible.yaml`, rendered by `scripts/personas/render.py` | Invented people, clients and projects. Each project appears three ways (CV, case study, README) under different names; some skills are implied only by code; decoy projects included |
| Past proposals (450, 150 per persona) | `scripts/personas/proposals.py` | Outcomes from GigSim's client rules plus 12% label noise; `label_source` on every row |
| Client hidden preferences | `scripts/data/build_world.py` | Drawn at random; public client statistics come from the 60K dataset when available |
| Evaluation jobs (dev 600, live stream 300, held-out test 300) | `scripts/data/generators.py`, `build_world.py` | Gold labels known by construction (see below) |

## Labels

| Kind | Share of dev | Gold label | Label source |
| --- | --- | --- | --- |
| good, good_vague | 50% | bid; evidence = the projects the requirements came from | construction |
| wrong_domain | 12% | skip; requirements share no skill with the persona | construction |
| off_domain | 8% | skip; real Kaggle "other" postings when available, else templates | domain rule / construction |
| below_floor | 8% | skip; budget under the persona's floor | construction |
| scam | 8% | skip; six scam patterns | construction |
| injection | 7% | unchanged fit label; instruction planted in text, hidden comment, zero-width characters or a screening question | construction |
| read_check | 2% | bid; a legitimate "start with the word X" request, not an attack | construction |
| duplicate | 5% | skip; repost of an earlier job by the same client | construction |

`fair_price_band` is P25–P75 of the market statistics for the job's domain, level and payment type, scaled by the square root of the number of requirements for fixed-price jobs.

**Hand verification.** `scripts/data/label_tool.py` reviews every scam and injection job plus a stratified sample of the rest. Agreement: (fill in after review).

## Splits

- `dev` (600): used for all tuning; labels committed in `evals/datasets/dev.jsonl`.
- `stream` (300): released into GigSim over simulated time for the live scouting demo.
- `test` (300): the held-out stand-in for the hidden test set; its labels are written outside the repository (`GIGBID_TEST_SPLIT_PATH`) and it is run once, at the end.

## Limitations

- Reply, interview and hire rates come from GigSim's simulated clients, whose rules we wrote; they measure fit to the simulator, not to real clients.
- Most evaluation jobs are templated, so their wording is less varied than real postings; real Kaggle posts are used where the label is unambiguous (off-domain).
- Construction labels inherit the generator's assumptions (for example, that a "wrong domain" job with no shared skills is always a skip).
- Domain tagging is keyword-based and will mislabel some postings.
- The 2023–2024 Upwork data predates current prices and categories.
