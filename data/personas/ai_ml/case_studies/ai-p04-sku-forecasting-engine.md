---
id: cs-ai-p04
project_id: ai-p04
type: case_study
persona: ai_ml
title: SKU forecasting engine
client_industry: retail
date: 2024-01
skills:
- forecasting
- LightGBM
- time series
synthetic: true
---
# SKU forecasting engine

**Context.** Buyers over-ordered slow movers and ran out of best sellers for 300 SKUs.

**What I did**

- Compared a Prophet baseline with a global LightGBM model using lag and promotion features
- Rolling-origin backtests over 52 weeks
- Weekly forecasts exported to the buyers' spreadsheet

**Results**

- MAPE: 14% versus 22% for the old method
- stock-outs on top 50 SKUs: down 30%

Tools: forecasting, LightGBM, time series

An excerpt from `forecast/backtest.py`:

```
import lightgbm as lgb
import numpy as np
import pandas as pd


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    for lag in (1, 2, 4, 8, 52):
        df[f"lag_{lag}"] = df.groupby("sku")["units"].shift(lag)
    df["promo_next_week"] = df.groupby("sku")["promo"].shift(-1)
    return df
```
