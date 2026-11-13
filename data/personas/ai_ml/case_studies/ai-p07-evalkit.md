---
id: cs-ai-p07
project_id: ai-p07
type: case_study
persona: ai_ml
title: evalkit
client_industry: open source
date: 2025-09
skills:
- LLM evaluation
- LLM-as-judge
- Cohen's kappa
synthetic: true
---
# evalkit

**Context.** Teams trusted LLM judges without checking them against human labels.

**What I did**

- A small library that runs rubric-based LLM judges over datasets
- Calibrates each judge against human labels and reports Cohen's kappa

**Results**

- GitHub stars: 140
- judges calibrated in the example notebook: 3, kappa 0.41 to 0.78

Tools: LLM evaluation, LLM-as-judge, Cohen's kappa

An excerpt from `evalkit/calibrate.py`:

```
"""Check an LLM judge against human labels before trusting it."""
from sklearn.metrics import cohen_kappa_score


def calibrate(judge_scores: list[int], human_scores: list[int]) -> dict:
    kappa = cohen_kappa_score(human_scores, judge_scores, weights="quadratic")
    disagreements = [i for i, (j, h) in enumerate(zip(judge_scores, human_scores)) if abs(j - h) >= 2]
    return {"kappa": round(kappa, 3), "n": len(human_scores), "large_disagreements": disagreements}
```
