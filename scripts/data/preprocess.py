"""Clean, deduplicate and tag the Kaggle data; build market statistics.

    uv run python -m scripts.data.preprocess            # writes data/processed/*
    uv run python -m scripts.data.preprocess --inspect  # show columns and the field mapping

Outputs (all git-ignored):
    jobs_clean.parquet      Upwork 60K jobs: cleaned text, PII masked, deduped, domain-tagged
    price_obs.parquet       one row per posted or realised price, with domain and source
    market_stats.json       P25/P50/P75 per domain x experience level x payment type
    scam_texts.jsonl        fraudulent postings (internal use; licence unknown)
    freelancer_rates.json   hourly-rate percentiles from the synthetic freelancer dataset
    preprocess_report.json  row counts after every step (copied into docs/DATA_CARD.md)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.data.sources import PROCESSED, SOURCES, Source, csv_files, map_columns
from scripts.data.text import (
    clean_text, exact_key, mostly_english, near_duplicate_groups, norm_level, parse_bool,
    parse_money, tag_domain,
)

PRICE_BOUNDS = {"fixed": (5, 100_000), "hourly": (3, 300)}


def read_source(src: Source, max_rows: int | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
    files = csv_files(src)
    if not files:
        return pd.DataFrame(), {}
    frames = []
    for f in files:
        df = (pd.read_parquet(f) if f.suffix == ".parquet"
              else pd.read_csv(f, low_memory=False, nrows=max_rows))
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    return df, map_columns(list(df.columns), src.columns)


def col(df: pd.DataFrame, mapping: dict[str, str], field: str) -> pd.Series:
    return df[mapping[field]] if field in mapping else pd.Series([None] * len(df), index=df.index)


def skills_from_categories(df: pd.DataFrame) -> list[list[str]]:
    cats = [c for c in df.columns if c.lower().startswith("category")]
    if not cats:
        return [[] for _ in range(len(df))]
    return [[str(v).strip() for v in row if isinstance(v, str) and v.strip()]
            for row in df[cats].itertuples(index=False)]


def process_jobs(report: dict) -> pd.DataFrame:
    src = SOURCES["upwork_60k"]
    raw, m = read_source(src)
    report["upwork_60k"] = {"raw_rows": len(raw), "mapping": m}
    if raw.empty:
        return pd.DataFrame()
    budget = col(raw, m, "budget").map(parse_money)
    lo = col(raw, m, "hourly_low").map(parse_money)
    hi = col(raw, m, "hourly_high").map(parse_money)
    ptype_raw = col(raw, m, "payment_type").astype(str).str.lower()
    is_hourly = ptype_raw.str.contains("hour") | (budget.isna() & (lo.notna() | hi.notna()))
    df = pd.DataFrame({
        "title": col(raw, m, "title").map(clean_text),
        "description": col(raw, m, "description").map(clean_text),
        "skills": skills_from_categories(raw),
        "experience_level": col(raw, m, "experience_level").map(norm_level),
        "payment_type": np.where(is_hourly, "hourly", "fixed"),
        "budget_max": np.where(is_hourly, np.nan, budget),
        "hourly_min": np.where(is_hourly, lo, np.nan),
        "hourly_max": np.where(is_hourly, hi.fillna(lo), np.nan),
        "connects": col(raw, m, "connects").map(parse_money),
        "applicants": col(raw, m, "applicants").map(parse_money),
        "client_country": col(raw, m, "client_country").fillna("").astype(str),
        "client_spent": col(raw, m, "client_spent").map(parse_money),
        "payment_verified": col(raw, m, "payment_verified").map(parse_bool),
        "client_rating": col(raw, m, "client_rating").map(parse_money),
        "client_reviews": col(raw, m, "client_reviews").map(parse_money),
        "duration": col(raw, m, "duration").fillna("").astype(str),
    })
    steps = report["upwork_60k"]
    df = df[(df.title.str.len() > 5) & (df.description.str.len() > 80)]
    steps["after_non_empty"] = len(df)
    df = df[df.description.map(mostly_english)]
    steps["after_english"] = len(df)
    df["exact_key"] = [exact_key(t, d[:500]) for t, d in zip(df.title, df.description,
                                                               strict=True)]
    df = df.drop_duplicates("exact_key")
    steps["after_exact_dedupe"] = len(df)
    groups = near_duplicate_groups(df.description.tolist())
    df = df[[g == i for i, g in enumerate(groups)]]
    steps["after_near_dedupe"] = len(df)
    df["domain"] = [tag_domain(t, d, s) for t, d, s in zip(df.title, df.description, df.skills,
                                                            strict=True)]
    steps["by_domain"] = df.domain.value_counts().to_dict()
    df["provenance"] = "kaggle:" + src.slug
    return df.reset_index(drop=True)


def process_prices(jobs: pd.DataFrame, report: dict) -> pd.DataFrame:
    rows = []
    if not jobs.empty:
        for r in jobs.itertuples():
            value = r.budget_max if r.payment_type == "fixed" else np.nanmean(
                [r.hourly_min, r.hourly_max])
            rows.append((r.title, r.domain, r.experience_level, r.payment_type, value,
                         "upwork_60k"))
    for key in ("upwork_50k", "upwork_200k"):
        src = SOURCES[key]
        raw, m = read_source(src)
        report[key] = {"raw_rows": len(raw), "mapping": m}
        if raw.empty:
            continue
        titles = col(raw, m, "title").map(clean_text)
        hourly = col(raw, m, "is_hourly").map(parse_bool).fillna(False).astype(bool)
        lo = col(raw, m, "hourly_low").map(parse_money)
        hi = col(raw, m, "hourly_high").map(parse_money)
        budget = col(raw, m, "budget").map(parse_money)
        seen = set()
        for t, h, a, b, bud in zip(titles, hourly, lo, hi, budget, strict=True):
            value = np.nanmean([x for x in (a, b) if x is not None] or [np.nan]) if h else bud
            k = exact_key(t, str(h), str(value))
            if k in seen:
                continue
            seen.add(k)
            rows.append((t, tag_domain(t), "any", "hourly" if h else "fixed", value, key))
        report[key]["unique_price_rows"] = len(seen)
    src = SOURCES["contracts_1_3m"]
    raw, m = read_source(src)
    report["contracts_1_3m"] = {"raw_rows": len(raw), "mapping": m}
    if not raw.empty:
        rate = col(raw, m, "hourly_rate").map(parse_money)
        if rate.isna().all():
            amount = col(raw, m, "amount").map(parse_money)
            hours = col(raw, m, "hours").map(parse_money)
            rate = amount / hours.replace(0, np.nan)
        titles = col(raw, m, "title").map(clean_text)
        for t, v in zip(titles, rate, strict=True):
            rows.append((t, tag_domain(t), "any", "hourly_realised", v, "contracts_1_3m"))
    prices = pd.DataFrame(rows, columns=["title", "domain", "experience_level", "payment_type",
                                         "value", "source"])
    prices = prices[prices.value.notna()]
    lo_f, hi_f = PRICE_BOUNDS["fixed"]
    lo_h, hi_h = PRICE_BOUNDS["hourly"]
    ok = np.where(prices.payment_type == "fixed", prices.value.between(lo_f, hi_f),
                  prices.value.between(lo_h, hi_h))
    report["price_rows_after_bounds"] = int(ok.sum())
    return prices[ok].reset_index(drop=True)


def market_stats(prices: pd.DataFrame, min_n: int = 20) -> list[dict]:
    out = []
    if prices.empty:
        return out
    groups = [prices]
    # Also aggregate every level as "any" so 50K/200K rows (no level) still contribute.
    any_level = prices.assign(experience_level="any")
    groups.append(any_level)
    both = pd.concat(groups, ignore_index=True)
    for (domain, level, ptype), g in both.groupby(["domain", "experience_level", "payment_type"]):
        if len(g) < min_n:
            continue
        q = g.value.quantile([0.25, 0.5, 0.75])
        out.append({"domain": domain, "experience_level": level, "payment_type": ptype,
                    "n": int(len(g)), "p25": round(float(q[0.25]), 2),
                    "p50": round(float(q[0.5]), 2), "p75": round(float(q[0.75]), 2),
                    "source": "+".join(sorted(g.source.unique()))})
    return out


def process_scams(report: dict, limit: int = 500) -> list[dict]:
    src = SOURCES["fake_jobs"]
    raw, m = read_source(src)
    report["fake_jobs"] = {"raw_rows": len(raw), "mapping": m}
    if raw.empty or "fraudulent" not in m:
        return []
    fraud = col(raw, m, "fraudulent").map(parse_bool).fillna(False)
    rows = []
    for t, d in zip(col(raw, m, "title")[fraud], col(raw, m, "description")[fraud], strict=True):
        text = clean_text(d)[:600]
        if len(text) > 80 and mostly_english(text):
            rows.append({"title": clean_text(t), "text": text, "source": src.slug})
        if len(rows) >= limit:
            break
    report["fake_jobs"]["fraudulent_kept"] = len(rows)
    return rows


def process_freelancer_rates(report: dict) -> dict:
    src = SOURCES["freelancer_success"]
    raw, m = read_source(src)
    report["freelancer_success"] = {"raw_rows": len(raw), "mapping": m}
    if raw.empty or "hourly_rate" not in m:
        return {}
    rate = col(raw, m, "hourly_rate").map(parse_money).dropna()
    q = rate.quantile([0.1, 0.25, 0.5, 0.75, 0.9])
    return {"overall": {str(k): round(float(v), 2) for k, v in q.items()}, "n": int(len(rate)),
            "synthetic": True}


def inspect() -> None:
    for key, src in SOURCES.items():
        raw, m = read_source(src, max_rows=5)
        print(f"\n== {key} ({src.slug}) files={len(csv_files(src))}")
        if raw.empty:
            print("   not downloaded")
            continue
        print("   columns:", list(raw.columns))
        print("   mapped: ", m)
        missing = sorted(set(src.columns) - set(m))
        if missing:
            print("   UNMAPPED:", missing, "-> add candidates in scripts/data/sources.py")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--out", type=Path, default=PROCESSED)
    args = parser.parse_args()
    if args.inspect:
        inspect()
        return
    args.out.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    jobs = process_jobs(report)
    if not jobs.empty:
        jobs.to_parquet(args.out / "jobs_clean.parquet", index=False)
    prices = process_prices(jobs, report)
    prices.to_parquet(args.out / "price_obs.parquet", index=False)
    stats = market_stats(prices)
    (args.out / "market_stats.json").write_text(json.dumps(stats, indent=2))
    scams = process_scams(report)
    with (args.out / "scam_texts.jsonl").open("w", encoding="utf-8") as fh:
        for r in scams:
            fh.write(json.dumps(r) + "\n")
    rates = process_freelancer_rates(report)
    (args.out / "freelancer_rates.json").write_text(json.dumps(rates, indent=2))
    report["market_stat_rows"] = len(stats)
    (args.out / "preprocess_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "mapping"}, indent=2,
                     default=str)[:3000])


if __name__ == "__main__":
    main()
