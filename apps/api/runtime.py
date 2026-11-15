"""Open the checkpoint backend shared by the API and the background worker."""

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.store.sqlite.aio import AsyncSqliteStore

from agent.config import REPO_ROOT, GigBidConfig, get_config
from apps.api.settings import load_simulator_account_tokens
from apps.api.workflow import WebWorkflow
from apps.api.workspace import WorkspaceState


@asynccontextmanager
async def open_workflow(
    *,
    settings: GigBidConfig | None = None,
    simulator_transport: httpx.AsyncBaseTransport | None = None,
    checkpoint_path: Path | None = None,
    use_queue: bool = True,
) -> AsyncIterator[WebWorkflow]:
    config = settings if settings is not None else get_config()
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=config.marketplace["gigsim"]["base_url"],
                timeout=10,
                transport=simulator_transport,
                trust_env=False,
            )
        )
        dsn = os.environ.get("GIGBID_CHECKPOINT_DSN")
        if dsn and checkpoint_path is None:
            saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(dsn))
            await saver.setup()
            store = await stack.enter_async_context(AsyncPostgresStore.from_conn_string(dsn))
        else:
            path = checkpoint_path or Path(
                os.environ.get("GIGBID_WEB_CHECKPOINT", str(REPO_ROOT / ".cache/gigbid-web.sqlite"))
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(path)))
            store = await stack.enter_async_context(AsyncSqliteStore.from_conn_string(str(path)))
        await store.setup()

        redis = None
        queue_url = (
            os.environ.get("GIGBID_QUEUE_URL") if use_queue and checkpoint_path is None else None
        )
        if queue_url:
            redis = await create_pool(RedisSettings.from_dsn(queue_url))
            stack.push_async_callback(redis.aclose)
        yield WebWorkflow(
            config,
            client,
            saver,
            load_simulator_account_tokens(),
            WorkspaceState(store),
            redis=redis,
        )
