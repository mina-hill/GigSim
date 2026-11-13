---
id: cs-ai-p03
project_id: ai-p03
type: case_study
persona: ai_ml
title: subscriber churn model
client_industry: telecom
date: 2024-05
skills:
- machine learning
- XGBoost
- SHAP
- feature engineering
synthetic: true
---
# subscriber churn model

For a telecom client (2024-03 to 2024-05). The retention team called customers at random and missed most of those about to leave.

Built features from 18 months of usage and billing data. Trained gradient-boosted trees with time-based validation. Explained each score with SHAP so agents knew why a customer was flagged.

Outcome: recall at the retention team's call capacity: up 18 points over the old rules; churn among contacted high-risk customers: down 7% in the first quarter.

An excerpt from `train.py`:

```
import pandas as pd
import shap
from sklearn.compose import ColumnTransformer
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

df = pd.read_sql("SELECT * FROM features.monthly_usage", "postgresql://analytics")
X, y = df.drop(columns=["churned_next_month"]), df["churned_next_month"]
pre = ColumnTransformer([("plan", OneHotEncoder(handle_unknown="ignore"), ["plan"])],
```
