from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook


@dag(schedule="0 2 * * *", start_date=datetime(2023, 11, 1), catchup=False,
     default_args={"retries": 2, "retry_delay": timedelta(minutes=10)})
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
