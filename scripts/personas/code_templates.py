"""Small but real code files for each synthetic repo, keyed by project `repo.kind`.

The code deliberately reveals skills that the case studies never name (Airflow, Celery,
Prisma...), so retrieval that indexes code separately (variant V5) has something to find.
Placeholders: {repo} repo name, {project} canonical project name, {industry} client industry.
"""

from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "rag_service": {
        "app/retrieve.py": '''"""Hybrid retrieval over policy chunks with page citations."""
from dataclasses import dataclass

import psycopg
from pgvector.psycopg import register_vector
from rank_bm25 import BM25Okapi


@dataclass
class Passage:
    doc_id: str
    page: int
    text: str
    score: float


def dense_search(conn: psycopg.Connection, query_vec, k: int = 20) -> list[Passage]:
    register_vector(conn)
    rows = conn.execute(
        "SELECT doc_id, page, text, 1 - (embedding <=> %s) FROM chunks ORDER BY embedding <=> %s LIMIT %s",
        (query_vec, query_vec, k),
    ).fetchall()
    return [Passage(*r) for r in rows]


def rrf(*rankings: list[Passage], k: int = 60) -> list[Passage]:
    scores: dict[tuple[str, int], float] = {}
    by_key: dict[tuple[str, int], Passage] = {}
    for ranking in rankings:
        for rank, p in enumerate(ranking):
            key = (p.doc_id, p.page)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            by_key[key] = p
    return [by_key[key] for key in sorted(scores, key=scores.get, reverse=True)]
''',
        "app/main.py": '''from fastapi import FastAPI
from pydantic import BaseModel

from app.answer import answer_with_citations

api = FastAPI(title="{repo}")


class Question(BaseModel):
    text: str


@api.post("/ask")
def ask(q: Question):
    result = answer_with_citations(q.text)
    if not result.citations:
        return {{"answer": None, "refused": True, "reason": "No supporting passage found"}}
    return {{"answer": result.text, "citations": result.citations}}
''',
        "Dockerfile": "FROM python:3.11-slim\nWORKDIR /srv\nCOPY . .\nRUN pip install -r "
                      "requirements.txt\nCMD [\"uvicorn\", \"app.main:api\", \"--host\", "
                      "\"0.0.0.0\"]\n",
    },
    "sklearn_pipeline": {
        "train.py": '''import pandas as pd
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
''',
    },
    "forecasting": {
        "forecast/backtest.py": '''import lightgbm as lgb
import numpy as np
import pandas as pd


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    for lag in (1, 2, 4, 8, 52):
        df[f"lag_{{lag}}"] = df.groupby("sku")["units"].shift(lag)
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
''',
    },
    "eval_harness": {
        "evalkit/calibrate.py": '''"""Check an LLM judge against human labels before trusting it."""
from sklearn.metrics import cohen_kappa_score


def calibrate(judge_scores: list[int], human_scores: list[int]) -> dict:
    kappa = cohen_kappa_score(human_scores, judge_scores, weights="quadratic")
    disagreements = [i for i, (j, h) in enumerate(zip(judge_scores, human_scores)) if abs(j - h) >= 2]
    return {{"kappa": round(kappa, 3), "n": len(human_scores), "large_disagreements": disagreements}}
''',
        "tests/test_calibrate.py": '''from evalkit.calibrate import calibrate


def test_perfect_agreement():
    assert calibrate([1, 2, 3, 4], [1, 2, 3, 4])["kappa"] == 1.0
''',
    },
    "vision_training": {
        "train.py": '''from ultralytics import YOLO
import cv2

model = YOLO("yolov8s.pt")
model.train(data="defects.yaml", epochs=80, imgsz=1024, close_mosaic=10)


def preprocess(path: str):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)
''',
    },
    "langgraph_agent": {
        "agent/graph.py": '''from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class EmailState(TypedDict, total=False):
    email: str
    intent: str
    draft: str
    approved: bool


def classify(state: EmailState) -> EmailState: ...
def draft_reply(state: EmailState) -> EmailState: ...


def approve(state: EmailState) -> EmailState:
    decision = interrupt({{"draft": state["draft"]}})
    return {{"approved": decision == "approve"}}


g = StateGraph(EmailState)
g.add_node("classify", classify)
g.add_node("draft", draft_reply)
g.add_node("approve", approve)
g.add_edge(START, "classify")
g.add_edge("classify", "draft")
g.add_edge("draft", "approve")
g.add_edge("approve", END)
''',
    },
    "stripe_webhooks": {
        "src/app/api/stripe/webhook/route.ts": '''import Stripe from "stripe";
import {{ prisma }} from "@/lib/prisma";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function POST(req: Request) {{
  const sig = req.headers.get("stripe-signature")!;
  const event = stripe.webhooks.constructEvent(await req.text(), sig, process.env.STRIPE_WEBHOOK_SECRET!);
  // Idempotency: Stripe retries webhooks, so remember every event we processed.
  const seen = await prisma.processedEvent.findUnique({{ where: {{ id: event.id }} }});
  if (seen) return new Response("duplicate", {{ status: 200 }});
  if (event.type === "customer.subscription.updated") {{
    const sub = event.data.object as Stripe.Subscription;
    await prisma.account.update({{ where: {{ stripeCustomerId: String(sub.customer) }}, data: {{ plan: sub.items.data[0].price.lookup_key }} }});
  }}
  await prisma.processedEvent.create({{ data: {{ id: event.id }} }});
  return new Response("ok");
}}
''',
    },
    "nest_rls": {
        "migrations/001_rls.sql": '''ALTER TABLE reports ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON reports
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
''',
        "src/reports/reports.service.ts": '''import {{ Injectable }} from "@nestjs/common";
import {{ DataSource }} from "typeorm";

@Injectable()
export class ReportsService {{
  constructor(private readonly db: DataSource) {{}}

  async forTenant(tenantId: string) {{
    return this.db.transaction(async (tx) => {{
      await tx.query("SELECT set_config('app.tenant_id', $1, true)", [tenantId]);
      return tx.query("SELECT * FROM reports ORDER BY created_at DESC LIMIT 100");
    }});
  }}
}}
''',
    },
    "socket_chat": {
        "server/index.ts": '''import {{ createAdapter }} from "@socket.io/redis-adapter";
import {{ createClient }} from "redis";
import {{ Server }} from "socket.io";

const io = new Server(4000, {{ cors: {{ origin: process.env.WEB_ORIGIN }} }});
const pub = createClient({{ url: process.env.REDIS_URL }});
const sub = pub.duplicate();
await Promise.all([pub.connect(), sub.connect()]);
io.adapter(createAdapter(pub, sub));

io.on("connection", (socket) => {{
  socket.on("join", (conversationId: string) => socket.join(conversationId));
  socket.on("typing", (conversationId: string) => socket.to(conversationId).emit("typing"));
}});
''',
    },
    "trpc_admin": {
        "src/server/routers/orders.ts": '''import {{ z }} from "zod";
import {{ protectedProcedure, router }} from "../trpc";

export const ordersRouter = router({{
  updateStatus: protectedProcedure
    .input(z.object({{ orderId: z.string().uuid(), status: z.enum(["held", "released"]) }}))
    .mutation(async ({{ ctx, input }}) => {{
      await ctx.prisma.auditLog.create({{ data: {{ actor: ctx.user.id, action: "order.status", payload: input }} }});
      return ctx.prisma.order.update({{ where: {{ id: input.orderId }}, data: {{ status: input.status }} }});
    }}),
}});
''',
    },
    "playwright_tests": {
        "tests/checkout.spec.ts": '''import {{ expect, test }} from "@playwright/test";

test("guest can check out with a test card", async ({{ page }}) => {{
  await page.goto("/listing/demo-bike");
  await page.getByRole("button", {{ name: "Buy now" }}).click();
  await page.getByLabel("Card number").fill("4242 4242 4242 4242");
  await page.getByRole("button", {{ name: "Pay" }}).click();
  await expect(page.getByText("Order confirmed")).toBeVisible();
}});
''',
        ".github/workflows/e2e.yml": "name: e2e\non: [pull_request]\njobs:\n  test:\n    "
                                     "runs-on: ubuntu-latest\n    steps:\n      - uses: "
                                     "actions/checkout@v4\n      - run: npx playwright test\n",
    },
    "dbt_model": {
        "models/marts/fct_orders.sql": '''with orders as (
    select * from {{{{ ref('stg_shopify__orders') }}}}
),
refunds as (
    select order_id, sum(amount) as refunded from {{{{ ref('stg_shopify__refunds') }}}} group by 1
)
select
    o.order_id,
    o.customer_id,
    o.ordered_at::date as order_date,
    o.gross_amount - coalesce(r.refunded, 0) as net_revenue
from orders o
left join refunds r using (order_id)
''',
        "models/marts/schema.yml": '''version: 2
models:
  - name: fct_orders
    columns:
      - name: order_id
        tests: [unique, not_null]
      - name: net_revenue
        tests: [not_null]
''',
    },
    "airflow_dag": {
        "dags/nightly_orders.py": '''from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook


@dag(schedule="0 2 * * *", start_date=datetime(2023, 11, 1), catchup=False,
     default_args={{"retries": 2, "retry_delay": timedelta(minutes=10)}})
def nightly_orders():
    @task
    def extract() -> int:
        hook = PostgresHook("source_db")
        return hook.get_first("SELECT count(*) FROM orders WHERE updated_at > now() - interval '1 day'")[0]

    @task
    def check_freshness(n: int) -> None:
        if n == 0:
            raise ValueError("No new orders in 24h: source export probably failed")

    check_freshness(extract())


nightly_orders()
''',
    },
    "ab_test": {
        "analysis.py": '''from scipy.stats import norm
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize, proportions_ztest

n_needed = NormalIndPower().solve_power(proportion_effectsize(0.050, 0.054), alpha=0.05, power=0.8)
stat, p = proportions_ztest(count=[1342, 1265], nobs=[24100, 24050], alternative="larger")
print(f"needed per arm: {{n_needed:.0f}}, z={{stat:.2f}}, p={{p:.3f}}")
''',
    },
    "pandas_analysis": {
        "segments.py": '''import pandas as pd
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
''',
    },
    "apps_script": {
        "Code.gs": '''function checkLowStock() {{
  const sheet = SpreadsheetApp.getActive().getSheetByName("Inventory");
  const rows = sheet.getDataRange().getValues().slice(1);
  const low = rows.filter(r => r[2] < r[3]).map(r => `${{r[0]}}: ${{r[2]}} left`);
  if (low.length) MailApp.sendEmail("coordinator@example.org", "Low stock", low.join("\\n"));
}}
''',
    },
}
