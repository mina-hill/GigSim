"""Server-Sent Events for the workspace bid progress viewer."""

from asyncio import timeout
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Header, Response
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from apps.api.routers.marketplace import NOT_FOUND, PersonaDep, ResourceId, WorkflowDep
from apps.api.streaming import BidTrace

router = APIRouter(prefix="/api/v1", tags=["Simulator workspace"])


@router.get(
    "/jobs/{job_id}/events",
    response_class=StreamingResponse,
    responses={
        **NOT_FOUND,
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
    },
)
async def bid_progress(
    runtime: WorkflowDep,
    persona: PersonaDep,
    job_id: ResourceId,
    last_event_id: Annotated[
        str | None, Header(pattern=r"^[0-9]{1,19}-[0-9]{1,19}$", alias="Last-Event-ID")
    ] = None,
) -> StreamingResponse:
    await runtime.get_run(persona, job_id)
    key = runtime.bid_event_key(persona, job_id)

    async def stream() -> AsyncIterator[str]:
        yield ": connected\n\n"
        try:
            async with (
                timeout(60),
                aclosing(runtime.progress.events(key, last_event_id or "0-0")) as events,
            ):
                async for record in events:
                    if record is None:
                        yield ": keep-alive\n\n"
                        continue
                    cursor, payload = record
                    yield f"id: {cursor}\nevent: progress\ndata: {payload}\n\n"
        except (TimeoutError, RedisError):
            # EventSource can reconnect with the last delivered cursor.
            return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}/trace", responses=NOT_FOUND)
async def download_bid_trace(
    runtime: WorkflowDep, persona: PersonaDep, job_id: ResourceId, response: Response
) -> BidTrace:
    """Export checkpoint timestamps and node statuses without workflow contents or credentials."""
    run = await runtime.get_run(persona, job_id)
    key = runtime.bid_event_key(persona, job_id)
    trace_id = sha256(key.encode()).hexdigest()[:24]
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = f'attachment; filename="gigbid-run-{trace_id}.json"'
    return BidTrace(
        trace_id=trace_id,
        job_id=job_id,
        persona=persona.view.id,
        captured_at=datetime.now(UTC),
        status=run.status,
        timeline=run.timeline,
    )
