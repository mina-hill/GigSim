"""Sign-in, sign-up and session status for the local workspace."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr, StringConstraints

from apps.api.auth.dependencies import require_local_store, validate_local_request
from apps.api.auth.store import (
    SESSION_COOKIE,
    SESSION_SECONDS,
    AccountConflict,
    LocalAuthStore,
    LocalUser,
)
from apps.api.schemas.workflow import ErrorView, PersonaId, PersonaView
from apps.api.workflow import WebWorkflow


class PrivateValidationRoute(APIRoute):
    """Avoid echoing submitted credentials or strings derived from them on validation errors."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "Check that the username, password and persona are valid."},
                    headers={"Cache-Control": "no-store"},
                )

        return handler


router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Local account"],
    route_class=PrivateValidationRoute,
    responses={403: {"model": ErrorView}, 422: {"model": ErrorView}},
)

Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$"
    ),
]
Password = Annotated[SecretStr, Field(min_length=12, max_length=256)]
AuthStore = Annotated[LocalAuthStore, Depends(require_local_store)]


class Credentials(BaseModel):
    username: Username
    password: Password


class Registration(Credentials):
    persona: PersonaId


class AuthSession(BaseModel):
    mode: Literal["demo", "local"]
    user: LocalUser | None = None
    available_personas: list[PersonaView] = Field(default_factory=list)


def _available(request: Request, store: LocalAuthStore) -> list[PersonaView]:
    workflow: WebWorkflow = request.app.state.workflow
    claimed = store.claimed_personas()
    return [persona for persona in workflow.list_personas() if persona.id not in claimed]


def _start(
    request: Request, response: Response, store: LocalAuthStore, user: LocalUser
) -> AuthSession:
    token = store.start_session(user, request.cookies.get(SESSION_COOKIE))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        max_age=SESSION_SECONDS,
    )
    response.headers["Cache-Control"] = "no-store"
    return AuthSession(mode="local", user=user)


@router.get("/session")
def session(request: Request, response: Response) -> AuthSession:
    response.headers["Cache-Control"] = "no-store"
    store: LocalAuthStore | None = request.app.state.auth_store
    if store is None:
        return AuthSession(mode="demo")
    validate_local_request(request)
    user = store.session_user(request.cookies.get(SESSION_COOKIE))
    return AuthSession(
        mode="local", user=user, available_personas=[] if user else _available(request, store)
    )


@router.post("/register", status_code=201, responses={409: {"model": ErrorView}})
def register(
    request: Request, response: Response, payload: Registration, store: AuthStore
) -> AuthSession:
    if payload.persona not in {persona.id for persona in _available(request, store)}:
        raise HTTPException(409, "That persona is unavailable. Select another persona.")
    try:
        user = store.register(
            payload.username.lower(), payload.password.get_secret_value(), payload.persona
        )
    except AccountConflict as error:
        raise HTTPException(409, str(error)) from error
    return _start(request, response, store, user)


@router.post("/login", responses={401: {"model": ErrorView}})
def login(
    request: Request, response: Response, payload: Credentials, store: AuthStore
) -> AuthSession:
    user = store.authenticate(payload.username.lower(), payload.password.get_secret_value())
    if user is None:
        raise HTTPException(401, "The username or password does not match.")
    return _start(request, response, store, user)


@router.post("/logout")
def logout(request: Request, response: Response, store: AuthStore) -> AuthSession:
    store.end_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, secure=request.url.scheme == "https", samesite="strict"
    )
    response.headers["Cache-Control"] = "no-store"
    return AuthSession(mode="local", available_personas=_available(request, store))
