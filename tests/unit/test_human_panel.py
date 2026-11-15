"""Check that blind vote decoding respects opposite proposal orderings."""

from evals.runners.human_panel import summarize


def test_pairwise_panel_decodes_counterbalanced_positions_and_report_ties():
    key = {"first": "evidence_first", "second": "question_first", "third": "question_first"}
    ballots = [
        {"first": "A", "second": "B", "third": "A"},
        {"first": "A", "second": "B", "third": "B"},
    ]
    report = summarize(key, ballots)
    assert report["reviewer_files"] == 2
    assert report["vote_counts"] == {"evidence_first": 5, "question_first": 1, "tie": 0}
    assert report["pair_majorities"] == {"evidence_first": 2, "question_first": 0, "tie": 1}
    assert report["exact_two_sided_sign_test_p"] == 0.5
