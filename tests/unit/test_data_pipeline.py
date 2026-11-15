"""The preprocessing pipeline on small CSVs shaped like the Kaggle files."""

import json
import random

import pandas as pd
import pytest

from scripts.data import preprocess, sources
from scripts.data.text import clean_text, near_duplicate_groups, parse_money, tag_domain


def test_text_helpers():
    t = clean_text("<p>Mail me at a.b@x.com or +1 (415) 555-0136, see https://x.io</p>")
    assert "a.b@x.com" not in t and "555-0136" not in t and "https" not in t
    assert parse_money("$1,200") == 1200 and parse_money("$10K+") == 10000
    assert parse_money(float("nan")) is None
    assert tag_domain("Build a RAG chatbot with LangChain") == "ai_ml"
    assert tag_domain("Power BI dashboard from Excel") == "data_analysis"
    assert tag_domain("Next.js SaaS with Stripe billing") == "fullstack_web"
    assert tag_domain("Logo design for bakery") == "other"
    groups = near_duplicate_groups(["the quick brown fox jumps over the lazy dog again and again",
                                    "the quick brown fox jumps over the lazy dog again and again!",
                                    "a totally different sentence about dashboards and sql"])
    assert groups == [0, 0, 2]


@pytest.fixture()
def fake_raw(tmp_path, monkeypatch):
    r = random.Random(1)
    monkeypatch.setattr(sources, "RAW", tmp_path)
    (tmp_path / "upwork_60k").mkdir()
    titles = ["Build RAG chatbot with LangChain", "Power BI sales dashboard", "React Next.js "
              "SaaS frontend", "Logo design"]
    rows = []
    for i in range(80):
        t = titles[i % 4]
        rows.append({
            "Title": t, "Description": f"{t}. We need help with project number {i} " * 6,
            "EX_level_demand": r.choice(["Entry level", "Intermediate", "Expert"]),
            "Payment_type": "Hourly" if i % 3 == 0 else "Fixed-price",
            "Job_Cost": f"${r.randint(100, 3000)}" if i % 3 else None,
            "Start_rate": r.randint(15, 40) if i % 3 == 0 else None,
            "End_rate": r.randint(40, 80) if i % 3 == 0 else None,
            "New_Connects_Num": r.choice([4, 8, 12, 16]), "Applicants_Num": r.randint(0, 50),
            "Client_Country": "United States", "Spent($)": "$10K+",
            "Payment_Situation": "Payment verified", "Rating": 4.8, "Feedback_Num": 12,
            "Category_1": "Python", "Category_2": "SQL", "Time_Limitation": "1 to 3 months",
        })
    rows.append(dict(rows[0]))                                   # exact duplicate
    rows.append({**rows[1], "Description": "Diseñar un panel de control " * 10})  # not English
    pd.DataFrame(rows).to_csv(tmp_path / "upwork_60k" / "jobs.csv", index=False)
    (tmp_path / "upwork_50k").mkdir()
    pd.DataFrame([{"title": "Excel dashboard and data analysis", "link": "x", "published_date":
                   "2024-02-01", "is_hourly": i % 2 == 0, "hourly_low": 20 + i % 10,
                   "hourly_high": 40, "budget": 300 + i, "country": "US"} for i in range(40)]
                 ).to_csv(tmp_path / "upwork_50k" / "upwork-jobs.csv", index=False)
    (tmp_path / "fake_jobs").mkdir()
    pd.DataFrame([{"title": "Easy money", "description": "Work from home, pay a small fee to "
                   "start and earn $500 a day guaranteed. " * 3, "fraudulent": 1},
                  {"title": "Accountant", "description": "Real job " * 30, "fraudulent": 0}]
                 ).to_csv(tmp_path / "fake_jobs" / "fake.csv", index=False)
    return tmp_path


def test_preprocess_end_to_end(fake_raw, tmp_path, monkeypatch):
    out = tmp_path / "processed"
    monkeypatch.setattr("sys.argv", ["preprocess", "--out", str(out)])
    preprocess.main()
    report = json.loads((out / "preprocess_report.json").read_text())
    jobs = pd.read_parquet(out / "jobs_clean.parquet")
    assert report["upwork_60k"]["raw_rows"] == 82
    assert report["upwork_60k"]["after_english"] == 81
    assert len(jobs) < 81                       # duplicates removed
    assert set(jobs.domain) >= {"ai_ml", "data_analysis", "fullstack_web", "other"}
    assert jobs.provenance.str.startswith("kaggle:").all()
    stats = json.loads((out / "market_stats.json").read_text())
    assert any(s["domain"] == "data_analysis" and s["experience_level"] == "any" for s in stats)
    scams = (out / "scam_texts.jsonl").read_text().splitlines()
    assert len(scams) == 1
