---
id: cs-da-p05
project_id: da-p05
type: case_study
persona: data_analyst
title: conversion experiment analysis
client_industry: saas
date: 2024-04
skills:
- A/B testing
- statistics
- experiment design
synthetic: true
---
# conversion experiment analysis

**What was the problem?**

The team wanted to know if a new pricing page really raised sign-ups.

**How did you solve it?**

power analysis before launch, then a two-proportion test with a pre-registered metric.

**Did it work?**

Yes: trial sign-up conversion: up 4.2% (p = 0.01).

An excerpt from `analysis.py`:

```
from scipy.stats import norm
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize, proportions_ztest

n_needed = NormalIndPower().solve_power(proportion_effectsize(0.050, 0.054), alpha=0.05, power=0.8)
stat, p = proportions_ztest(count=[1342, 1265], nobs=[24100, 24050], alternative="larger")
print(f"needed per arm: {n_needed:.0f}, z={stat:.2f}, p={p:.3f}")
```
