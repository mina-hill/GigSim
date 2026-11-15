import json
from asyncio import Event, Queue, create_task, gather, timeout
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from agent.config import get_config
from agent.nodes.bid import OfflineBidNodes
from apps.api.main import create_app
from apps.api.settings import load_simulator_account_tokens
from gigsim.settings import get_settings

WORLD = Path(__file__).resolve().parents[2] / "gigsim" / "fixtures" / "mini_world"
JOBS = "/api/v1/jobs"
JOB = f"{JOBS}/job_web_stripe"
FULLSTACK = {"persona": "fullstack"}
AI_ML = {"persona": "ai_ml"}


def test_simulator_account_tokens_use_the_environment_or_synthetic_defaults(monkeypatch):
    monkeypatch.delenv("GIGSIM_API_TOKENS", raising=False)
    assert load_simulator_account_tokens() == {
        "persona_ai_ml": "dev-token-ai",
        "persona_fullstack": "dev-token-web",
        "persona_data_analyst": "dev-token-data",
    }
    monkeypatch.setenv("GIGSIM_API_TOKENS", " custom:persona_fullstack, malformed, :missing ")
    assert load_simulator_account_tokens() == {"persona_fullstack": "custom"}
    monkeypatch.setenv("GIGSIM_API_TOKENS", "")
    assert load_simulator_account_tokens() == {}


@pytest.fixture
async def simulator(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGSIM_DB_URL", "sqlite:///:memory:")
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    monkeypatch.setenv("GIGSIM_LATENCY_MS_MAX", "0")
    monkeypatch.setenv("GIGSIM_ADMIN_TOKEN", "web-test-admin")
    monkeypatch.setenv("GIGSIM_CLIENT_MODE", "rules")
    monkeypatch.setenv(
        "GIGSIM_API_TOKENS", "test-fullstack:persona_fullstack,test-ai:persona_ai_ml"
    )
    get_settings.cache_clear()
    from gigsim.app import app

    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://gigsim.test"
            ) as client:
                seed = await client.post(
                    "/admin/seed",
                    headers={"X-Admin-Token": "web-test-admin"},
                    json={"world_path": str(WORLD)},
                )
                seed.raise_for_status()
                yield transport, tmp_path / "web.sqlite"
    finally:
        get_settings.cache_clear()


def web_app(transport, checkpoint_path, *, mode="gigsim"):
    settings = get_config()
    settings = settings.model_copy(
        update={
            "marketplace": {
                **settings.marketplace,
                "mode": mode,
                "gigsim": {"base_url": "http://gigsim.test"},
            },
        }
    )
    return create_app(
        simulator_transport=transport, checkpoint_path=checkpoint_path, settings=settings
    )


@asynccontextmanager
async def web_client(transport, checkpoint_path, *, mode="gigsim"):
    app = web_app(transport, checkpoint_path, mode=mode)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://web.test"
        ) as client,
    ):
        yield client


@asynccontextmanager
async def sse_messages(app, path, *, after=None):
    """Observe ASGI messages as they are sent. httpx's ASGI transport buffers the stream."""
    messages = Queue()
    disconnect = Event()
    headers = [] if after is None else [(b"last-event-id", after.encode())]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "scheme": "http",
        "headers": headers,
        "query_string": b"persona=fullstack",
    }

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    task = create_task(app(scope, receive, messages.put))
    try:
        yield messages
    finally:
        disconnect.set()
        await task


async def next_progress(messages):
    while True:
        message = await messages.get()
        if message["type"] == "http.response.body" and message["body"].startswith(b"id: "):
            cursor, label, data = message["body"].decode().strip().splitlines()
            assert label == "event: progress"
            return cursor.removeprefix("id: "), json.loads(data.removeprefix("data: "))


async def test_settings_budget_portfolio_pipeline_and_analytics_survive_restart(simulator):
    transport, checkpoint_path = simulator
    settings_url = "/api/v1/workspace/settings"
    pipeline_url = "/api/v1/workspace/pipeline"
    analytics_url = "/api/v1/workspace/analytics"
    prefs = {
        "weekly_connects_budget": 12,
        "portfolio": [
            {
                "title": "Subscription migration",
                "summary": (
                    "I built a subscription migration with webhook replay and billing tests."
                ),
                "skills": ["Next.js", "Stripe", "PostgreSQL", "TypeScript"],
            }
        ],
        "saved_searches": [{"name": "Billing", "query": "Stripe", "domain": "fullstack_web"}],
    }
    async with web_client(transport, checkpoint_path) as client:
        saved = await client.put(settings_url, params=FULLSTACK, json=prefs)
        saved.raise_for_status()
        assert saved.json()["preferences"]["weekly_connects_budget"] == 12
        stranger = await client.get(settings_url, params=AI_ML)
        assert stranger.json()["preferences"]["portfolio"] == []
        filtered = await client.get(
            JOBS, params={**FULLSTACK, "q": "Stripe", "domain": "fullstack_web"}
        )
        assert [job["job_id"] for job in filtered.json()] == ["job_web_stripe"]
        prepared = await client.post(f"{JOB}/run", params=FULLSTACK)
        prepared.raise_for_status()
        pending = prepared.json()["pending"]
        assert "Subscription migration" in pending["draft"]["cover_letter"]
        board = await client.get(pipeline_url, params=FULLSTACK)
        assert [card["stage"] for card in board.json()] == ["drafted"]

    async with web_client(transport, checkpoint_path) as client:
        saved = await client.get(settings_url, params=FULLSTACK)
        assert saved.json()["preferences"]["saved_searches"][0]["name"] == "Billing"
        digest = pending["proposal_digest"]
        approval = {"action": "approve", "proposal_digest": digest}
        blocked_budget = await client.put(
            settings_url,
            params=FULLSTACK,
            json={
                **prefs,
                "weekly_connects_budget": 0,
            },
        )
        blocked_budget.raise_for_status()
        blocked = await client.post(f"{JOB}/decision", params=FULLSTACK, json=approval)
        assert blocked.status_code == 409
        restored = await client.put(settings_url, params=FULLSTACK, json=prefs)
        restored.raise_for_status()
        submitted = await client.post(f"{JOB}/decision", params=FULLSTACK, json=approval)
        submitted.raise_for_status()
        assert submitted.json()["status"] == "done"
        board = await client.get(pipeline_url, params=FULLSTACK)
        assert [card["stage"] for card in board.json()] == ["submitted"]
        metrics = await client.get(analytics_url, params=FULLSTACK)
        metrics.raise_for_status()
        assert metrics.json()["categories"] == [
            {
                "domain": "fullstack_web",
                "submitted": 1,
                "replied": 0,
                "hired": 0,
                "connects_spent": 12,
            }
        ]
        assert metrics.json()["proposals"][0]["preparation_ms"] is not None
        assert (await client.get(settings_url, params=FULLSTACK)).json()[
            "weekly_connects_spent"
        ] == 12


async def test_stream_exposes_live_tasks_and_replays_after_cursor(simulator, monkeypatch):
    transport, checkpoint_path = simulator
    app = web_app(transport, checkpoint_path)
    release = Event()
    analyze = OfflineBidNodes.analyze

    async def slow_analysis(self, job):
        await release.wait()
        return await analyze(self, job)

    monkeypatch.setattr(OfflineBidNodes, "analyze", slow_analysis)
    async with (
        timeout(10),
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://web.test"
        ) as client,
    ):
        path = f"{JOB}/events"
        async with sse_messages(app, path) as messages:
            response = await messages.get()
            assert response["status"] == 200
            assert (b"content-type", b"text/event-stream; charset=utf-8") in response["headers"]
            assert (b"cache-control", b"no-store, no-transform") in response["headers"]
            run = create_task(client.post(f"{JOB}/run", params=FULLSTACK))
            try:
                while True:
                    cursor, event = await next_progress(messages)
                    if event.get("node") == "job_analyst":
                        break
                assert event["status"] == "started"
                assert set(event) == {"kind", "node", "status", "task_id"}
                assert not run.done()
            finally:
                release.set()
                prepared = await run

        assert prepared.json()["status"] == "awaiting_approval"
        async with sse_messages(app, path, after=cursor) as messages:
            replay_cursor, _ = await next_progress(messages)
            assert int(replay_cursor.split("-")[0]) > int(cursor.split("-")[0])
            while True:
                _, event = await next_progress(messages)
                if event.get("node") == "approval" and event["status"] == "waiting_for_approval":
                    break

        trace = await client.get(f"{JOB}/trace", params=FULLSTACK)
        assert trace.status_code == 200
        data = trace.json()
        assert set(data) == {"trace_id", "job_id", "persona", "captured_at", "status", "timeline"}
        assert data["status"] == "awaiting_approval"
        assert datetime.fromisoformat(data["captured_at"]).utcoffset() is not None
        assert data["timeline"] == prepared.json()["timeline"]
        assert data["timeline"][-1]["created_at"]
        assert trace.headers["content-disposition"] == (
            f'attachment; filename="gigbid-run-{data["trace_id"]}.json"'
        )
        other = (await client.get(f"{JOB}/trace", params=AI_ML)).json()
        assert other["trace_id"] != data["trace_id"]
        assert not other["timeline"]


async def test_inbox_run_and_submission_remain_available_after_api_restart(simulator):
    transport, checkpoint_path = simulator
    async with web_client(transport, checkpoint_path) as client:
        assert (await client.get("/health/deps")).json()["gigsim"]["status"] == "up"
        personas = (await client.get("/api/v1/personas")).json()
        assert {persona["id"] for persona in personas} == {"fullstack", "ai_ml"}
        jobs = (await client.get(JOBS, params={**FULLSTACK, "q": "Stripe"})).json()
        assert jobs[0]["job_id"] == "job_web_stripe"
        assert jobs[0]["run_status"] == "idle"
        assert (await client.get(f"{JOB}/run", params=FULLSTACK)).json()["status"] == "idle"
        prepared = (await client.post(f"{JOB}/run", params=FULLSTACK)).json()
        assert prepared["status"] == "awaiting_approval"
        assert any(
            set(step["nodes"]) == {"job_analyst", "risk_assessor", "fit_matcher"}
            for step in prepared["timeline"]
        )
        assert prepared["timeline"][-1]["status"] == "waiting_for_approval"
        assert (await client.get("/api/v1/pipeline", params=FULLSTACK)).json() == []

    async with web_client(transport, checkpoint_path) as client:
        detail = (await client.get(JOB, params=FULLSTACK)).json()
        pending = detail["run"]["pending"]
        assert pending["proposal_digest"] == prepared["pending"]["proposal_digest"]
        assert (await client.get(f"{JOB}/run", params=AI_ML)).json()["status"] == "idle"
        decision = {"action": "approve", "proposal_digest": pending["proposal_digest"]}
        response = await client.post(f"{JOB}/decision", params=FULLSTACK, json=decision)
        assert response.status_code == 200
        outcome = response.json()["outcome"]
        assert outcome["cover_letter"] == pending["submission"]["cover_letter"]
        assert outcome["connects_spent"] == pending["preview"]["connects_cost"] == 12
        assert (await client.get("/api/v1/pipeline", params=FULLSTACK)).json() == [outcome]
        assert (await client.get("/api/v1/pipeline", params=AI_ML)).json() == []
        assert (await client.get("/api/v1/messages", params=FULLSTACK)).status_code == 200


async def test_invalid_stale_and_concurrent_decisions_do_not_overwrite_run(simulator):
    transport, checkpoint_path = simulator
    async with web_client(transport, checkpoint_path) as client:
        run = (await client.post(f"{JOB}/run", params=FULLSTACK)).json()
        pending = run["pending"]
        digest = pending["proposal_digest"]
        invalid = await client.post(
            f"{JOB}/decision",
            params=FULLSTACK,
            json={"action": "reject", "reason": " ", "proposal_digest": digest},
        )
        assert invalid.status_code == 422

        edited = (
            pending["submission"]["cover_letter"] + "\n\nThank you for considering my proposal."
        )
        edited_response = await client.post(
            f"{JOB}/decision",
            params=FULLSTACK,
            json={"action": "edit", "text": edited, "proposal_digest": digest},
        )
        assert edited_response.status_code == 200
        revised = edited_response.json()["pending"]
        assert revised["proposal_digest"] != digest
        assert revised["submission"]["cover_letter"] == edited
        stale = await client.post(
            f"{JOB}/decision",
            params=FULLSTACK,
            json={"action": "approve", "proposal_digest": digest},
        )
        assert stale.status_code == 409
        assert (await client.get("/api/v1/pipeline", params=FULLSTACK)).json() == []

        approval = {"action": "approve", "proposal_digest": revised["proposal_digest"]}
        decisions = await gather(
            *(client.post(f"{JOB}/decision", params=FULLSTACK, json=approval) for _ in range(2))
        )
        assert sorted(response.status_code for response in decisions) == [200, 409]
        proposals = (await client.get("/api/v1/pipeline", params=FULLSTACK)).json()
        assert len(proposals) == 1
        assert proposals[0]["cover_letter"] == edited


async def test_unknown_resources_return_not_found(simulator):
    transport, checkpoint_path = simulator
    async with web_client(transport, checkpoint_path) as client:
        assert (await client.get(JOBS, params={"persona": "other"})).status_code == 404
        assert (await client.get(f"{JOBS}/missing/run", params=FULLSTACK)).status_code == 404
        assert (await client.get("/api/v1/messages/missing", params=FULLSTACK)).status_code == 404


async def test_failed_external_step_is_resumable_until_review(simulator):
    transport, checkpoint_path = simulator
    fail_preview = True

    async def simulator_request(request):
        if fail_preview and request.url.path.endswith("/connects-quote"):
            raise httpx.ReadError("Lost the Connects quote response", request=request)
        return await transport.handle_async_request(request)

    async with web_client(httpx.MockTransport(simulator_request), checkpoint_path) as client:
        assert (await client.post(f"{JOB}/run", params=FULLSTACK)).status_code == 503
        paused = (await client.get(f"{JOB}/run", params=FULLSTACK)).json()
        assert paused["status"] == "needs_attention"
        assert paused["resumable"] is True
        assert paused["pending"] is None

        fail_preview = False
        resumed = (await client.post(f"{JOB}/run", params=FULLSTACK)).json()
        assert resumed["status"] == "awaiting_approval"
        assert resumed["resumable"] is False
        assert resumed["pending"] is not None
        assert (await client.get("/api/v1/pipeline", params=FULLSTACK)).json() == []


async def test_unavailable_or_live_marketplace_keeps_health_accessible(tmp_path):
    def offline(request):
        raise httpx.ConnectError("Simulator offline", request=request)

    transport = httpx.MockTransport(offline)
    async with web_client(transport, tmp_path / "offline.sqlite") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/health/deps")).json()["gigsim"]["status"] == "down"
        assert (await client.get(JOBS, params=FULLSTACK)).status_code == 503

    async with web_client(transport, tmp_path / "live.sqlite", mode="upwork") as client:
        assert (await client.get("/health/deps")).json()["marketplace_mode"]["status"] == "upwork"
        assert (await client.get("/api/v1/personas")).status_code == 503
