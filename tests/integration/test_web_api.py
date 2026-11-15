from asyncio import gather
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from agent.config import get_config
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


@asynccontextmanager
async def web_client(transport, checkpoint_path, *, mode="gigsim"):
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
    app = create_app(
        simulator_transport=transport, checkpoint_path=checkpoint_path, settings=settings
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://web.test"
        ) as client,
    ):
        yield client


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
