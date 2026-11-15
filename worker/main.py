"""Redis-backed bid runs and opt-in scouting for the synthetic workspace."""

import os
from contextlib import AsyncExitStack
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from apps.api.runtime import open_workflow
from apps.api.workflow import WebWorkflow


async def startup(ctx: dict[str, Any]) -> None:
    stack = AsyncExitStack()
    try:
        ctx["workflow"] = await stack.enter_async_context(open_workflow(use_queue=False))
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
    for persona in workflow.sessions.values():
        jobs = await workflow.list_jobs(persona, q=None, limit=12, cursor=0)
        unseen = [job for job in jobs if job.run_status == "idle"][:3]
        for job in unseen:
            queued = await ctx["redis"].enqueue_job(
                "run_bid",
                persona.view.id,
                job.job_id,
                _job_id=workflow.queue_id(persona, job.job_id),
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
