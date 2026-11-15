"""FastAPI entry point for the local GigSim workspace."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import ValidationError

from agent.config import REPO_ROOT, GigBidConfig, get_config
from apps.api.routers.marketplace import router
from apps.api.schemas.workflow import DependencyStatus, DependencyView, ErrorView, HealthView
from apps.api.settings import load_simulator_account_tokens
from apps.api.workflow import WebWorkflow


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorView(detail=detail).model_dump())


def create_app(
    *,
    simulator_transport: httpx.AsyncBaseTransport | None = None,
    checkpoint_path: Path | None = None,
    settings: GigBidConfig | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings if settings is not None else get_config()
        path = checkpoint_path or Path(
            os.environ.get("GIGBID_WEB_CHECKPOINT", str(REPO_ROOT / ".cache/gigbid-web.sqlite"))
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        async with (
            httpx.AsyncClient(
                base_url=config.marketplace["gigsim"]["base_url"],
                timeout=10,
                transport=simulator_transport,
                trust_env=False,
            ) as client,
            AsyncSqliteSaver.from_conn_string(str(path)) as saver,
        ):
            app.state.workflow = WebWorkflow(config, client, saver, load_simulator_account_tokens())
            yield

    app = FastAPI(title="GigBid API", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(httpx.HTTPStatusError)
    async def simulator_status(request: Request, error: httpx.HTTPStatusError) -> JSONResponse:
        status = error.response.status_code
        if status == 404:
            return _error(404, "The simulator resource was not found.")
        if status == 409:
            return _error(409, "The simulator refused the action because its state changed.")
        return _error(503, "The simulator could not complete the request. Try again.")

    @app.exception_handler(httpx.RequestError)
    async def simulator_connection(request: Request, error: httpx.RequestError) -> JSONResponse:
        return _error(503, "The simulator is unreachable. Try again.")

    @app.exception_handler(ValidationError)
    async def external_data(request: Request, error: ValidationError) -> JSONResponse:
        return _error(503, "Simulator or saved workflow data could not be read.")

    @app.get("/health")
    async def health() -> HealthView:
        return HealthView()

    @app.get("/health/deps", response_model_exclude_none=True)
    async def health_deps(request: Request) -> DependencyView:
        runtime: WebWorkflow = request.app.state.workflow
        mode = runtime.settings.marketplace["mode"]
        gigsim = DependencyStatus(status="down", detail="The web demo requires GigSim mode.")
        if mode == "gigsim":
            try:
                response = await runtime.client.get("/health", timeout=3)
            except httpx.HTTPError as error:
                gigsim = DependencyStatus(status="down", detail=type(error).__name__)
            else:
                if response.is_success:
                    gigsim = DependencyStatus(status="up")
                else:
                    gigsim = DependencyStatus(status="down", detail=f"HTTP {response.status_code}")
        return DependencyView(
            gigsim=gigsim,
            model_profile=DependencyStatus(status=runtime.settings.models.profile),
            marketplace_mode=DependencyStatus(status=mode),
        )

    return app


app = create_app()
