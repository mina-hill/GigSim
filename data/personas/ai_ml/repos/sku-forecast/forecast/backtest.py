import lightgbm as lgb
import numpy as np
import pandas as pd


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    for lag in (1, 2, 4, 8, 52):
        df[f"lag_{lag}"] = df.groupby("sku")["units"].shift(lag)
    df["promo_next_week"] = df.groupby("sku")["promo"].shift(-1)
    return df


def rolling_origin(df: pd.DataFrame, weeks: int = 52) -> float:
    errors = []
    for cutoff in sorted(df["week"].unique())[-weeks:]:
        train, test = df[df.week < cutoff], df[df.week == cutoff]
        model = lgb.LGBMRegressor(objective="tweedie", n_estimators=600)
        model.fit(train.filter(like="lag_"), train["units"])
        pred = model.predict(test.filter(like="lag_"))
        errors.append(np.mean(np.abs(pred - test["units"]) / np.maximum(test["units"], 1)))
    return float(np.mean(errors))
