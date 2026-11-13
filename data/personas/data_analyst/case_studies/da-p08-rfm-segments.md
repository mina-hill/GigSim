---
id: cs-da-p08
project_id: da-p08
type: case_study
persona: data_analyst
title: RFM segments
client_industry: fitness
date: 2024-09
skills:
- customer segmentation
- RFM analysis
synthetic: true
---
# RFM segments

For a fitness client (2024-08 to 2024-09). Every member got the same marketing emails.

RFM features and clustering into five segments with plain-language profiles.

Outcome: email click-through: up 9%.

An excerpt from `segments.py`:

```
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

members = pd.read_csv("members.csv", parse_dates=["last_visit"])
rfm = members.groupby("member_id").agg(
    recency=("last_visit", lambda s: (pd.Timestamp("2024-09-01") - s.max()).days),
    frequency=("visit_id", "count"),
    monetary=("spend", "sum"),
)
```
