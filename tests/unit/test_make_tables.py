import json

import pytest

from scripts.make_tables import BEGIN, DOCUMENTS, END, sync_results


@pytest.fixture
def report_tree(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "evals" / "results").mkdir(parents=True)
    for document in DOCUMENTS:
        (tmp_path / document).write_text(
            f"Intro\n\n{BEGIN}\n\nEvaluation results are pending.\n\n{END}\n\nOutro\n",
            encoding="utf-8",
        )
    return tmp_path


def test_regenerate_tables_from_result_artifact(report_tree):
    artifact = report_tree / "evals" / "results" / "smoke.report.json"
    artifact.write_text(json.dumps({
        "schema_version": 1,
        "experiment": "Synthetic smoke run",
        "scope": "A local fixture set.",
        "command": "python -m evals.runners.smoke",
        "metrics": [
            {"name": "Cases | approved", "value": 2, "denominator": 3},
            {"name": "Decision F1", "value": 0.8},
        ],
    }), encoding="utf-8")

    documents = [report_tree / document for document in DOCUMENTS]
    assert sync_results(report_tree, check=True) == documents
    assert "pending" in documents[0].read_text(encoding="utf-8")
    assert sync_results(report_tree, check=False) == documents
    assert sync_results(report_tree, check=True) == []
    for index, document in enumerate(documents):
        rendered = document.read_text(encoding="utf-8")
        prefix = "" if index == 0 else "../"
        assert f"]({prefix}evals/results/smoke.report.json)" in rendered
        assert r"| Cases \| approved | 2 / 3 |" in rendered
        assert "| Decision F1 | 0.8 |" in rendered
        assert rendered.startswith("Intro\n") and rendered.endswith("Outro\n")


def test_check_fails_on_missing_markers(report_tree):
    (report_tree / DOCUMENTS[1]).write_text("unfinished\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one pair"):
        sync_results(report_tree, check=True)


def test_check_rejects_report_with_impossible_count(report_tree):
    results = report_tree / "evals" / "results"
    # Only explicitly designated report artifacts are consumed.
    (results / "diagnostic.json").write_text("invalid report data", encoding="utf-8")
    assert sync_results(report_tree, check=True) == []
    (results / "example.report.json").write_text(json.dumps({
        "schema_version": 1,
        "experiment": "Synthetic smoke run",
        "scope": "A local fixture set.",
        "command": "python -m evals.runners.smoke",
        "metrics": [{"name": "Cases covered", "value": 4, "denominator": 3}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match=r"example\.report\.json has an invalid report schema"):
        sync_results(report_tree, check=True)
