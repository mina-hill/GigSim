import json
from pathlib import Path

from scripts.data.build_world import build
from scripts.data.label_tool import review_sample, summarise


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x]


def test_build_world_small(tmp_path):
    summary = build(per_persona=40, processed=tmp_path, dev_out=tmp_path / "dev.jsonl",
                    test_out=tmp_path / "test_gold.jsonl")
    assert summary["dev_jobs"] == 120 and summary["test_jobs"] == 60
    gold = _jsonl(tmp_path / "dev.jsonl")
    jobs = {j["job_id"]: j for j in _jsonl(tmp_path / "gigsim_world" / "jobs.jsonl")}
    test_ids = {g["job_id"] for g in _jsonl(tmp_path / "test_gold.jsonl")}
    assert not test_ids & set(jobs)                  # test jobs never enter the dev world
    for g in gold:
        job = jobs[g["job_id"]]
        assert "persona" not in job and "kind" not in job       # no label leaks into the world
        if g["kind"] == "good":
            assert g["bid_or_skip"] == "bid" and g["gold_evidence"]
        if g["kind"] == "scam":
            assert g["is_scam"] and g["bid_or_skip"] == "skip"
        if g["kind"] == "duplicate":
            assert g["duplicate_of"] in jobs
        lo, hi = g["fair_price_band"]
        assert 0 < lo < hi
    kinds = {g["kind"] for g in gold}
    assert {"good", "scam", "injection", "duplicate", "below_floor"} <= kinds


def test_build_is_deterministic(tmp_path):
    a = build(per_persona=20, processed=tmp_path / "a", dev_out=tmp_path / "a.jsonl",
              test_out=tmp_path / "at.jsonl")
    b = build(per_persona=20, processed=tmp_path / "b", dev_out=tmp_path / "b.jsonl",
              test_out=tmp_path / "bt.jsonl")
    assert a == b
    assert (tmp_path / "a.jsonl").read_text() == (tmp_path / "b.jsonl").read_text()


def test_review_sample_includes_all_risky_jobs():
    gold = [{"job_id": str(i), "kind": k, "is_scam": k == "scam", "has_injection": k ==
             "injection", "read_check": False} for i, k in enumerate(
        ["scam"] * 3 + ["injection"] * 2 + ["good"] * 50 + ["off_domain"] * 10)]
    picked = review_sample(gold, n_other=12)
    assert sum(g["kind"] in {"scam", "injection"} for g in picked) == 5
    s = summarise([{"kind": "good", "agree": True}, {"kind": "good", "agree": False}])
    assert s["agreement"] == 0.5
