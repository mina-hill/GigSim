"""Resolve authenticated local users while preserving the isolated review fixture."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from agent.config import REPO_ROOT
from apps.api.auth.store import SESSION_COOKIE, LocalAuthStore, LocalUser

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def store_from_env() -> LocalAuthStore | None:
    mode = os.environ.get("GIGBID_AUTH_MODE", "demo")
    if mode == "demo":
        return None
    if mode != "local":
        raise ValueError("GIGBID_AUTH_MODE must be demo or local.")
    return LocalAuthStore(
        Path(os.environ.get("GIGBID_AUTH_DB", REPO_ROOT / ".cache/local-accounts/auth.sqlite"))
    )


def validate_local_request(request: Request) -> None:
    # SameSite protects the cookie, while origin/fetch metadata protects login and signup too.
    web_origin = os.environ.get("GIGBID_AUTH_WEB_ORIGIN", "").rstrip("/")
    host = request.headers.get("host", "")
    request_origin = f"{request.url.scheme}://{host}"
    try:
        local_host = urlsplit(request_origin).hostname in _LOOPBACK
    except ValueError:
        local_host = False
    if not local_host and request_origin != web_origin:
        raise HTTPException(403, "Use the configured GigBid browser address.")
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site not in {None, "same-origin", "none"} or (
        origin is not None and origin not in {request_origin, web_origin}
    ):
        raise HTTPException(403, "Open GigBid directly to change your workspace.")


def require_local_store(request: Request) -> LocalAuthStore:
    store: LocalAuthStore | None = request.app.state.auth_store
    if store is None:
        raise HTTPException(404, "This workspace uses the review demo.")
    validate_local_request(request)
    return store


def workspace_access(request: Request) -> LocalUser | None:
    store: LocalAuthStore | None = request.app.state.auth_store
    if store is None:
        return None
    validate_local_request(request)
    user = store.session_user(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "Sign in to open your workspace.")
    return user


WorkspaceAccess = Annotated[LocalUser | None, Depends(workspace_access)]
