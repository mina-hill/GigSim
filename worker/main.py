"""Redis-backed bid runs and opt-in scouting for the synthetic workspace."""

import os
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from apps.api.runtime import open_workflow
from apps.api.schemas.workspace import SavedSearch
from apps.api.streaming import BidProgress
from apps.api.workflow import WebWorkflow


async def startup(ctx: dict[str, Any]) -> None:
    stack = AsyncExitStack()
    try:
        workflow = await stack.enter_async_context(open_workflow(use_queue=False))
        workflow.progress = BidProgress(ctx["redis"])
        ctx["workflow"] = workflow
    except BaseException:
        await stack.aclose()
        raise
    ctx["stack"] = stack


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["stack"].aclose()


async def run_bid(ctx: dict[str, Any], persona_id: str, job_id: str) -> str:
    workflow: WebWorkflow = ctx["workflow"]
    result = await workflow.start_run(workflow.persona(persona_id), job_id)
    return result.status


async def scout(ctx: dict[str, Any]) -> int:
    """Stage a bounded batch of unseen jobs and leave each decision for review."""
    workflow: WebWorkflow = ctx["workflow"]
    count = 0
    minute = int(datetime.now(UTC).timestamp() // 60)
    for persona in workflow.sessions.values():
        prefs = await workflow.workspace.preferences(workflow.tenant(persona))
        searches = prefs.saved_searches or [
            SavedSearch(name="Inbox", enabled=True, interval_minutes=30),
        ]
        unseen: dict[str, None] = {}
        for search in searches:
            if not search.enabled or minute % search.interval_minutes != 0:
                continue
            jobs = await workflow.list_jobs(
                persona,
                q=search.query or None,
                domain=search.domain or None,
                payment_type=None if search.payment_type == "any" else search.payment_type,
                limit=12,
                cursor=0,
            )
            unseen.update({job.job_id: None for job in jobs if job.run_status == "idle"})
        for job_id in list(unseen)[:3]:
            queued = await ctx["redis"].enqueue_job(
                "run_bid",
                persona.view.id,
                job_id,
                _job_id=workflow.queue_id(persona, job_id),
                _expires=600,
            )
            count += queued is not None
    return count


class WorkerSettings:
    functions = [run_bid]
    cron_jobs = (
        [cron(scout, minute={0, 30}, second=0)]
        if os.environ.get("GIGBID_SCOUT_ENABLED", "").lower() in {"true", "1"}
        else []
    )
    redis_settings = RedisSettings.from_dsn(
        os.environ.get("GIGBID_QUEUE_URL", "redis://localhost:6379/0")
    )
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1
    keep_result = 0
    job_timeout = 90
