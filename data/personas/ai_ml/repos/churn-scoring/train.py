import pandas as pd
import shap
from sklearn.compose import ColumnTransformer
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

df = pd.read_sql("SELECT * FROM features.monthly_usage", "postgresql://analytics")
X, y = df.drop(columns=["churned_next_month"]), df["churned_next_month"]
pre = ColumnTransformer([("plan", OneHotEncoder(handle_unknown="ignore"), ["plan"])],
                        remainder="passthrough")
model = XGBClassifier(n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8)
for train_idx, test_idx in TimeSeriesSplit(n_splits=4).split(X):
    model.fit(pre.fit_transform(X.iloc[train_idx]), y.iloc[train_idx])
explainer = shap.TreeExplainer(model)
