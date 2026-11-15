"""Exercise the review packet and chance-corrected agreement without a model API."""

import json

import pytest

from evals.runners.judge_calibration import agreement, build_cases, judge, prepare, score


def test_packet_keeps_review_labels_empty_and_does_not_overwrite_user_ratings(tmp_path):
    prepare(tmp_path)
    path = tmp_path / "reviewer-labels.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len({row["case_id"] for row in rows}) == 30
    assert all(row["rating"] is None for row in rows)
    with pytest.raises(ValueError, match="missing reviewer rating"):
        cases = build_cases()
        score(cases, path, judge(cases, "verifier"))

    rows[0]["rating"] = "supported"
    filled = "\n".join(json.dumps(row) for row in rows)
    path.write_text(filled, encoding="utf-8")
    prepare(tmp_path)
    assert path.read_text(encoding="utf-8") == filled


def test_agreement_scores_partially_supported_as_a_distinct_category():
    reviewer = {"a": "supported", "b": "partly_supported", "c": "unsupported"}
    predictions = {"a": "supported", "b": "unsupported", "c": "unsupported"}
    result = agreement(reviewer, predictions)
    assert result["observed_agreement"] == 0.6667
    assert result["cohens_kappa"] == 0.5
    assert result["confusion_matrix_human_by_automated"]["partly_supported"]["unsupported"] == 1


def test_agreement_handles_undefined_chance_adjustment_and_rejects_mismatched_ids():
    assert agreement({"a": "supported"}, {"a": "supported"})["cohens_kappa"] is None
    with pytest.raises(ValueError, match="same nonempty set"):
        agreement({"a": "supported"}, {"b": "supported"})
