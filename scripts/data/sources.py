"""The Kaggle datasets GigBid uses, where they go on disk, and how their columns map.

Column names differ between datasets and some pages do not list them, so each logical field has
a list of candidate column names. Matching ignores case, spaces, underscores and punctuation.
Run `uv run python -m scripts.data.preprocess --inspect` after downloading to see the real
columns and what each field mapped to; add a candidate here if something did not map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"


@dataclass(frozen=True)
class Source:
    key: str
    slug: str
    license: str
    use: str
    columns: dict[str, list[str]] = field(default_factory=dict)

    @property
    def folder(self) -> Path:
        return RAW / self.key

    @property
    def url(self) -> str:
        return f"https://www.kaggle.com/datasets/{self.slug}"


SOURCES: dict[str, Source] = {s.key: s for s in [
    Source(
        "upwork_60k", "ahmedmyalo/upwork-freelance-jobs-60k", "Apache 2.0",
        "Job texts, skill tags, Connects, client statistics",
        {
            "title": ["title", "job_title"],
            "description": ["description", "job_description", "desc"],
            "experience_level": ["ex_level_demand", "experience_level", "level", "experience"],
            "payment_type": ["payment_type", "job_type", "type", "hourly"],
            "budget": ["job_cost", "budget", "fixed_price", "price"],
            "hourly_low": ["start_rate", "hourly_low", "rate_low", "min_rate"],
            "hourly_high": ["end_rate", "hourly_high", "rate_high", "max_rate"],
            "connects": ["new_connects_num", "connects_num", "connects", "required_connects"],
            "applicants": ["applicants_num", "applicants", "proposals"],
            "client_country": ["client_country", "country"],
            "client_spent": ["spent", "spent_", "client_spent", "total_spent"],
            "payment_verified": ["payment_situation", "payment_verified", "verified"],
            "client_rating": ["rating", "client_rating"],
            "client_reviews": ["feedback_num", "reviews", "client_reviews", "feedbacks"],
            "duration": ["time_limitation", "duration", "project_length"],
            "posted": ["date", "published_date", "posted", "time"],
        },
    ),
    Source(
        "upwork_50k", "asaniczka/upwork-job-postings-dataset-2024-50k-records", "ODC-By",
        "Posted budgets and hourly ranges for market statistics",
        {
            "title": ["title"],
            "is_hourly": ["is_hourly", "hourly"],
            "hourly_low": ["hourly_low", "rate_low"],
            "hourly_high": ["hourly_high", "rate_high"],
            "budget": ["budget", "fixed_budget"],
            "country": ["country"],
            "posted": ["published_date", "date"],
        },
    ),
    Source(
        "upwork_200k", "asaniczka/all-jobs-on-upwork-200k-plus", "ODC-By",
        "Larger pool for market statistics (deduplicated against the 50K set)",
        {
            "title": ["title"],
            "is_hourly": ["is_hourly", "hourly"],
            "hourly_low": ["hourly_low", "rate_low"],
            "hourly_high": ["hourly_high", "rate_high"],
            "budget": ["budget", "fixed_budget"],
            "country": ["country"],
            "posted": ["published_date", "date"],
        },
    ),
    Source(
        "contracts_1_3m", "asaniczka/freelance-contracts-dataset-1-3-million-entries", "ODC-By",
        "Realised hourly rates and amounts paid",
        {
            "title": ["title", "job_title", "contract_title"],
            "hours": ["hours", "total_hours", "hours_worked"],
            "amount": ["amount", "total_amount", "amount_paid", "earnings", "paid"],
            "hourly_rate": ["hourly_rate", "rate", "price_per_hour"],
        },
    ),
    Source(
        "freelancer_success", "sarveshchhetri/freelancer-success-earnings-and-churn-prediction",
        "Apache 2.0 (synthetic)", "Realistic freelancer rate distribution",
        {
            "hourly_rate": ["hourly_rate", "rate", "hourly_rate_usd"],
            "category": ["job_category", "category", "skill_category", "primary_skill"],
        },
    ),
    Source(
        "fake_jobs", "adityammantri/trial-1", "Unknown (internal use only, not redistributed)",
        "Scam wording patterns for the scam generator",
        {
            "title": ["title"],
            "description": ["description", "company_profile", "requirements"],
            "fraudulent": ["fraudulent", "fake", "is_fake", "label"],
        },
    ),
]}


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def map_columns(columns: list[str], wanted: dict[str, list[str]]) -> dict[str, str]:
    """Logical field -> actual column name, for the fields that could be matched."""
    by_norm = {norm(c): c for c in columns}
    out: dict[str, str] = {}
    for logical, candidates in wanted.items():
        for cand in candidates:
            if norm(cand) in by_norm:
                out[logical] = by_norm[norm(cand)]
                break
        else:
            for cand in candidates:          # looser: candidate is a prefix of a column
                hit = next((c for n, c in by_norm.items() if n.startswith(norm(cand))), None)
                if hit:
                    out[logical] = hit
                    break
    return out


def csv_files(source: Source) -> list[Path]:
    if not source.folder.exists():
        return []
    return sorted(p for p in source.folder.rglob("*") if p.suffix.lower() in {".csv", ".parquet"})
