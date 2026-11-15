"""Bounded, replayable progress events shared by the API and bid worker."""

from asyncio import Condition, timeout
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from logging import getLogger
from time import time_ns
from typing import Literal

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from apps.api.schemas.workflow import PersonaId, RunStatus, RunStep

_MAX_EVENTS = 160
_MAX_THREADS = 256
_HEARTBEAT_SECONDS = 12
_log = getLogger(__name__)


class BidTrace(BaseModel):
    trace_id: str
    job_id: str
    persona: PersonaId
    captured_at: datetime
    status: RunStatus
    timeline: list[RunStep]


class BidProgressEvent(BaseModel):
    kind: Literal["run", "node"]
    status: Literal["started", "completed", "waiting_for_approval", "error"]
    node: str | None = None
    task_id: str | None = None

    @classmethod
    def for_task(cls, task: Mapping[str, object]) -> "BidProgressEvent | None":
        node = task["name"]
        task_id = task["id"]
        if not isinstance(node, str) or not isinstance(task_id, str) or node.startswith("__"):
            return None
        status: Literal["started", "completed", "waiting_for_approval", "error"]
        if "input" in task:
            status = "started"
        elif task.get("error"):
            status = "error"
        elif task.get("interrupts"):
            status = "waiting_for_approval"
        else:
            status = "completed"
        # Task payloads include inputs, outputs and errors. Observers get only progress metadata.
        return cls(kind="node", status=status, node=node, task_id=task_id)


class BidProgress:
    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis
        self._memory: OrderedDict[str, deque[tuple[int, str]]] = OrderedDict()
        self._condition = Condition()
        self._serial = 0

    async def publish(self, key: str, event: BidProgressEvent) -> None:
        body = event.model_dump_json()
        if self._redis is not None:
            try:
                async with timeout(1), self._redis.pipeline() as pipe:
                    pipe.xadd(key, {"data": body}, maxlen=_MAX_EVENTS, approximate=False)
                    pipe.expire(key, 3600)
                    await pipe.execute()
            except (RedisError, TimeoutError):
                # A failed observation must not change the outcome of a bid or approval.
                _log.warning("Could not publish bid progress to Redis.")
            return

        async with self._condition:
            self._serial = max(time_ns(), self._serial + 1)
            events = self._memory.setdefault(key, deque(maxlen=_MAX_EVENTS))
            events.append((self._serial, body))
            self._memory.move_to_end(key)
            if len(self._memory) > _MAX_THREADS:
                self._memory.popitem(last=False)
            self._condition.notify_all()

    async def events(self, key: str, after: str = "0-0") -> AsyncIterator[tuple[str, str] | None]:
        if self._redis is not None:
            async for record in self._redis_events(key, after):
                yield record
            return

        cursor = int(after.split("-", 1)[0])
        while True:
            try:
                async with timeout(_HEARTBEAT_SECONDS), self._condition:
                    await self._condition.wait_for(
                        lambda cursor=cursor: (
                            bool(self._memory.get(key)) and self._memory[key][-1][0] > cursor
                        )
                    )
                    batch = [
                        (serial, body) for serial, body in self._memory[key] if serial > cursor
                    ]
            except TimeoutError:
                yield None
                continue
            for serial, body in batch:
                cursor = serial
                yield f"{serial}-0", body

    async def _redis_events(self, key: str, cursor: str) -> AsyncIterator[tuple[str, str] | None]:
        assert self._redis is not None
        while True:
            records = await self._redis.xread(
                {key: cursor}, count=64, block=_HEARTBEAT_SECONDS * 1000
            )
            if not records:
                yield None
                continue
            for _, batch in records:
                for event_id, data in batch:
                    cursor = event_id.decode()
                    yield cursor, data[b"data"].decode()
