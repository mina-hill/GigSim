import pytest

from evals.runners.x2 import exact_mcnemar, summarize


@pytest.mark.parametrize(
    ("evidence_only", "question_only", "expected"),
    [(0, 0, 1), (4, 4, 1), (8, 0, 0.0078125), (0, 8, 0.0078125), (3, 1, 0.625)],
)
def test_exact_mcnemar_two_sided(evidence_only, question_only, expected):
    assert exact_mcnemar(evidence_only, question_only) == expected


def test_reply_rate_uses_paired_outcomes_as_denominator():
    pairs = [
        {"evidence_reply": True, "question_reply": True},
        {"evidence_reply": True, "question_reply": False},
        {"evidence_reply": False, "question_reply": True},
        {"evidence_reply": True, "question_reply": False},
        {"evidence_reply": False, "question_reply": False},
    ]
    result = summarize(pairs)
    assert result["both_replied"] == 1
    assert result["evidence_only"] == 2
    assert result["question_only"] == 1
    assert result["neither_replied"] == 1
    assert result["evidence_replies"] == 3
    assert result["question_replies"] == 2
    assert result["reply_rate_difference_pp"] == 20
