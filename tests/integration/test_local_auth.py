"""Exercise sign-in, session persistence and ownership against the frozen simulator."""

from contextlib import asynccontextmanager

import httpx

from apps.api.auth.store import SESSION_COOKIE
from tests.integration.test_web_api import simulator as simulator
from tests.integration.test_web_api import web_app

AUTH = "/api/v1/auth"
JOB = "/api/v1/jobs/job_web_stripe"


@asynccontextmanager
async def local_clients(transport, checkpoint):
    app = web_app(transport, checkpoint)
    proxy = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=proxy, base_url="http://127.0.0.1:8000") as first,
        httpx.AsyncClient(transport=proxy, base_url="http://127.0.0.1:8000") as second,
    ):
        yield first, second


def local_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGBID_AUTH_MODE", "local")
    monkeypatch.setenv("GIGBID_AUTH_DB", str(tmp_path / "users.sqlite"))
    monkeypatch.setenv("GIGBID_AUTH_WEB_ORIGIN", "http://127.0.0.1:3000")


def registration(username, persona):
    return {"username": username, "password": "a unique test passphrase", "persona": persona}


async def test_owned_approvals_and_sessions_survive_restart(simulator, monkeypatch, tmp_path):
    local_mode(monkeypatch, tmp_path)
    transport, checkpoint = simulator
    fullstack = {"persona": "fullstack"}
    async with local_clients(transport, checkpoint) as (first, second):
        assert (await first.get("/api/v1/personas")).status_code == 401
        anonymous = (await first.get(f"{AUTH}/session")).json()
        assert {item["id"] for item in anonymous["available_personas"]} == {"fullstack", "ai_ml"}

        register = await first.post(
            f"{AUTH}/register",
            json=registration("First.User", "fullstack"),
            headers={"origin": "http://127.0.0.1:3000", "sec-fetch-site": "same-origin"},
        )
        assert register.status_code == 201
        assert register.json()["user"]["username"] == "first.user"
        cookie = register.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        token = first.cookies.get(SESSION_COOKIE)
        assert token is not None
        assert token not in register.text
        assert register.headers["cache-control"] == "no-store"

        conflict = await second.post(f"{AUTH}/register", json=registration("second", "fullstack"))
        assert conflict.status_code == 409
        duplicate = await second.post(f"{AUTH}/register", json=registration("FIRST.USER", "ai_ml"))
        assert duplicate.status_code == 409
        assert (
            await second.post(f"{AUTH}/register", json=registration("second", "ai_ml"))
        ).status_code == 201
        assert [item["id"] for item in (await second.get("/api/v1/personas")).json()] == ["ai_ml"]

        ready = await first.post(f"{JOB}/run", params=fullstack)
        assert ready.status_code == 200
        approval = ready.json()["pending"]["proposal_digest"]
        assert (await second.get(f"{JOB}/run", params=fullstack)).status_code == 403
        assert (await second.get(f"{JOB}/events", params=fullstack)).status_code == 403
        assert (
            await second.post(
                f"{JOB}/decision",
                params=fullstack,
                json={"action": "approve", "proposal_digest": approval},
            )
        ).status_code == 403
        assert (await second.get("/api/v1/workspace/settings", params=fullstack)).status_code == 403

    async with local_clients(transport, checkpoint) as (first, second):
        first.cookies.set(SESSION_COOKIE, token, domain="127.0.0.1", path="/")
        session = (await first.get(f"{AUTH}/session")).json()
        assert session["user"]["persona"] == "fullstack"
        saved = await first.get(f"{JOB}/run", params=fullstack)
        assert saved.json()["pending"]["proposal_digest"] == approval

        assert (await first.post(f"{AUTH}/logout")).json()["user"] is None
        second.cookies.set(SESSION_COOKIE, token, domain="127.0.0.1", path="/")
        assert (await second.get(f"{JOB}/run", params=fullstack)).status_code == 401
        auth = {"username": "FIRST.USER", "password": "incorrect test login"}
        assert (await first.post(f"{AUTH}/login", json=auth)).status_code == 401
        auth["password"] = registration("first.user", "fullstack")["password"]
        assert (await first.post(f"{AUTH}/login", json=auth)).status_code == 200
        assert first.cookies.get(SESSION_COOKIE) != token


async def test_local_auth_rejects_cross_origin_and_does_not_echo_password(
    simulator, monkeypatch, tmp_path
):
    local_mode(monkeypatch, tmp_path)
    async with local_clients(*simulator) as (client, _):
        user = registration("example", "fullstack")
        for headers in (
            {"origin": "https://other.test", "sec-fetch-site": "cross-site"},
            {"origin": "https://other.test"},
            {"host": "other.test", "sec-fetch-site": "same-origin"},
        ):
            response = await client.post(f"{AUTH}/register", json=user, headers=headers)
            assert response.status_code == 403
        user["password"] = "secret!"
        response = await client.post(f"{AUTH}/register", json=user)
        assert response.status_code == 422
        assert "secret!" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get(f"{AUTH}/session")).json()["user"] is None


async def test_explicit_demo_profile_preserves_fixture_access(simulator, monkeypatch):
    monkeypatch.setenv("GIGBID_AUTH_MODE", "demo")
    async with local_clients(*simulator) as (client, _):
        assert (await client.get(f"{AUTH}/session")).json()["mode"] == "demo"
        assert (await client.get("/api/v1/personas")).status_code == 200
        assert (
            await client.post(f"{AUTH}/register", json=registration("example", "fullstack"))
        ).status_code == 404
