---
id: cs-da-p04
project_id: da-p04
type: case_study
persona: data_analyst
title: orders ETL
client_industry: logistics
date: 2024-01
skills:
- ETL
- scheduling
- data quality checks
synthetic: true
---
# orders ETL

**Context.** Reports ran on data exported by hand whenever someone remembered.

**What I did**

- Scheduled jobs pulling orders and shipments into a reporting database every night
- Row-count and freshness checks with alerts on failure

**Results**

- reports on stale data: from weekly to never in six months

Tools: ETL, scheduling, data quality checks

An excerpt from `dags/nightly_orders.py`:

```
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook


@dag(schedule="0 2 * * *", start_date=datetime(2023, 11, 1), catchup=False,
     default_args={"retries": 2, "retry_delay": timedelta(minutes=10)})
def nightly_orders():
    @task
```
