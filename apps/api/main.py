"""FastAPI entry point for the local GigSim workspace."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from redis.exceptions import RedisError

from agent.config import GigBidConfig
from apps.api.auth.dependencies import store_from_env
from apps.api.routers.auth import router as auth_router
from apps.api.routers.marketplace import router
from apps.api.routers.streaming import router as streaming_router
from apps.api.routers.workspace import router as workspace_router
from apps.api.runtime import open_workflow
from apps.api.schemas.workflow import DependencyStatus, DependencyView, ErrorView, HealthView
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
        async with open_workflow(
            settings=settings,
            simulator_transport=simulator_transport,
            checkpoint_path=checkpoint_path,
        ) as runtime:
            app.state.auth_store = store_from_env()
            app.state.workflow = runtime
            yield

    app = FastAPI(title="GigBid API", version="0.1.0", lifespan=lifespan)
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(streaming_router)
    app.include_router(workspace_router)

    @app.exception_handler(httpx.HTTPStatusError)
    async def simulator_status(request: Request, error: httpx.HTTPStatusError) -> JSONResponse:
        status = error.response.status_code
        if status == 404:
            return _error(404, "The simulator resource was not found.")
        if status == 409:
            return _error(409, "The simulator refused the action because its state changed.")
        if status == 429:
            return _error(503, "The simulator rate limit was reached. Wait briefly and try again.")
        return _error(503, "The simulator could not complete the request. Try again.")

    @app.exception_handler(httpx.RequestError)
    async def simulator_connection(request: Request, error: httpx.RequestError) -> JSONResponse:
        return _error(503, "The simulator is unreachable. Try again.")

    @app.exception_handler(ValidationError)
    async def external_data(request: Request, error: ValidationError) -> JSONResponse:
        return _error(503, "Simulator or saved workflow data could not be read.")

    @app.exception_handler(RedisError)
    async def queue_connection(request: Request, error: RedisError) -> JSONResponse:
        return _error(503, "The background queue is unavailable. Try again.")

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
