"""GigBid API (FastAPI). Phase 0: health endpoints only; routers arrive in Phase 5."""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI

from agent.config import get_config

app = FastAPI(title="GigBid API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gigbid-api"}


@app.get("/health/deps")
async def health_deps() -> dict[str, dict[str, str]]:
    """State of each dependency. The UI's degraded-mode banner reads this."""
    cfg = get_config()
    deps: dict[str, dict[str, str]] = {}
    gigsim_url = cfg.marketplace["gigsim"]["base_url"]
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{gigsim_url}/health")
        deps["gigsim"] = {"status": "up" if r.status_code == 200 else f"http {r.status_code}"}
    except httpx.HTTPError as exc:
        deps["gigsim"] = {"status": "down", "detail": type(exc).__name__}
    deps["model_profile"] = {"status": cfg.models.profile}
    deps["marketplace_mode"] = {"status": os.environ.get("MARKETPLACE_MODE", "gigsim")}
    return deps
