"""Check an LLM judge against human labels before trusting it."""
from sklearn.metrics import cohen_kappa_score


def calibrate(judge_scores: list[int], human_scores: list[int]) -> dict:
    kappa = cohen_kappa_score(human_scores, judge_scores, weights="quadratic")
    disagreements = [i for i, (j, h) in enumerate(zip(judge_scores, human_scores)) if abs(j - h) >= 2]
    return {"kappa": round(kappa, 3), "n": len(human_scores), "large_disagreements": disagreements}
