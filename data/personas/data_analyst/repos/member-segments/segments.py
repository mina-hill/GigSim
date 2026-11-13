import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

members = pd.read_csv("members.csv", parse_dates=["last_visit"])
rfm = members.groupby("member_id").agg(
    recency=("last_visit", lambda s: (pd.Timestamp("2024-09-01") - s.max()).days),
    frequency=("visit_id", "count"),
    monetary=("spend", "sum"),
)
labels = KMeans(n_clusters=5, n_init=10, random_state=0).fit_predict(StandardScaler().fit_transform(rfm))
rfm["segment"] = labels
