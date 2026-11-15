import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

from gigsim.app import app
from gigsim.db import get_engine
from gigsim.settings import get_settings
from mcp_servers.audit import AuditLog
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.server import create_marketplace_server
from mcp_servers.portfolio.server import create_portfolio_server
from mcp_servers.workspace.server import WorkspaceStore, create_workspace_server
from rag.corpus import load_corpus

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "gigsim" / "fixtures" / "mini_world"


async def _call(client: ClientSession, tool: str, **arguments):
    result = await client.call_tool(tool, arguments=arguments)
    assert not result.isError, result.content
    assert result.structuredContent is not None
    return result.structuredContent


@pytest.fixture
async def gigsim_port(monkeypatch):
    monkeypatch.setenv("GIGSIM_DB_URL", "sqlite:///:memory:")
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    monkeypatch.setenv("GIGSIM_LATENCY_MS_MAX", "0")
    monkeypatch.setenv("GIGSIM_CLIENT_MODE", "rules")
    monkeypatch.setenv("GIGSIM_POLICY_VERSION", "v1")
    monkeypatch.setenv("GIGSIM_RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("GIGSIM_API_TOKENS", "mcp-test-marketplace:persona_fullstack")
    monkeypatch.setenv("GIGSIM_ADMIN_TOKEN", "mcp-test-admin")
    get_settings.cache_clear()
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://sim.test"
            ) as client,
        ):
            seed = await client.post(
                "/admin/seed",
                json={"world_path": str(WORLD)},
                headers={
                    "X-Admin-Token": "mcp-test-admin",
                },
            )
            seed.raise_for_status()
            yield GigSimAdapter(client, token="mcp-test-marketplace")
    finally:
        get_engine().dispose()
        get_settings.cache_clear()


async def test_marketplace_dry_run_and_submit_is_idempotent(gigsim_port, tmp_path):
    audit = AuditLog(tmp_path / "audit.db")
    server = create_marketplace_server(gigsim_port, audit)
    proposal = {
        "job_id": "job_web_stripe",
        "bid_amount": 1500,
        "cover_letter": "I can review the billing setup and test retries on Stripe webhooks.",
    }
    async with create_connected_server_and_client_session(server) as client:
        before = await _call(client, "get_connects_balance")
        preview = await _call(
            client, "create_proposal_draft", proposal=proposal, idempotency_key="draft"
        )
        assert preview["dry_run"] is True and preview["payload"]["bid_amount"] == 1500
        assert preview["quote"]["connects_cost"] == 12
        assert preview["quote"]["balance_after"] == before["balance"] - 12
        assert await gigsim_port.list_proposals() == []

        created = await _call(
            client,
            "create_proposal_draft",
            proposal=proposal,
            idempotency_key="draft",
            dry_run=False,
        )
        proposal_id = created["proposal"]["proposal_id"]
        review = await _call(
            client, "submit_proposal", proposal_id=proposal_id, idempotency_key="send"
        )
        assert review["quote"] == preview["quote"]
        assert (await gigsim_port.get_connects_balance()).balance == before["balance"]

        arguments = {
            "proposal_id": proposal_id,
            "idempotency_key": "send",
            "dry_run": False,
            "approved_quote": review["quote"],
        }
        first = await _call(client, "submit_proposal", **arguments)
        second = await _call(client, "submit_proposal", **arguments)
        assert first == second
        assert first["proposal"]["connects_spent"] == 12
        assert len(await gigsim_port.list_proposals()) == 1
        assert (await gigsim_port.get_connects_balance()).balance == before["balance"] - 12

    with closing(sqlite3.connect(audit.path)) as database:
        events = database.execute("SELECT operation, outcome FROM audit_log ORDER BY event_id")
        assert len(events.fetchall()) == 6
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            database.execute("DELETE FROM audit_log")
    assert proposal["cover_letter"].encode() not in audit.path.read_bytes()


async def test_workspace_holds_and_email_drafts_survive_restart_without_leaking_audit(tmp_path):
    path = tmp_path / "workspace.db"
    audit = AuditLog(tmp_path / "audit.db")
    server = create_workspace_server(WorkspaceStore(path), audit)
    date_range = {"start": "2026-11-16T09:00:00+05:00", "end": "2026-11-16T11:00:00+05:00"}
    time_query = {"range": date_range, "tz": "Asia/Karachi", "duration": 30}
    async with create_connected_server_and_client_session(server) as client:
        slots = (await _call(client, "free_slots", **time_query))["slots"]
        slot = slots[0]
        hold = {
            "slot": slot,
            "title": "Interview",
            "client_ref": "sample-client",
            "idempotency_key": "hold",
        }
        assert (await _call(client, "create_tentative_hold", **hold))["hold_id"] is None
        assert (await _call(client, "free_slots", **time_query))["slots"] == slots
        reserved = await _call(client, "create_tentative_hold", dry_run=False, **hold)
        email = {
            "to": "recipient@example.test",
            "subject": "Next steps",
            "body": "Private draft body",
            "idempotency_key": "email",
        }
        assert (await _call(client, "create_email_draft", **email))["draft_id"] is None
        saved = await _call(client, "create_email_draft", dry_run=False, **email)

    reopened = create_workspace_server(WorkspaceStore(path), audit)
    async with create_connected_server_and_client_session(reopened) as client:
        assert slot not in (await _call(client, "free_slots", **time_query))["slots"]
        assert await _call(client, "create_tentative_hold", dry_run=False, **hold) == reserved
        assert await _call(client, "create_email_draft", dry_run=False, **email) == saved
        conflict = await client.call_tool(
            "create_tentative_hold",
            arguments={
                **hold,
                "dry_run": False,
                "idempotency_key": "another-hold",
            },
        )
        assert conflict.isError
        different = await client.call_tool(
            "create_email_draft",
            arguments={
                **email,
                "dry_run": False,
                "subject": "Different message",
            },
        )
        assert different.isError
    assert b"recipient@example.test" not in audit.path.read_bytes()
    assert b"Private draft body" not in audit.path.read_bytes()


async def test_portfolio_search_resources_and_prompts_are_scoped_to_corpus():
    corpus = load_corpus(ROOT / "data" / "personas" / "fullstack")
    server = create_portfolio_server(corpus)
    async with create_connected_server_and_client_session(server) as client:
        results = await _call(
            client,
            "search_evidence",
            requirement="Stripe subscription billing webhooks",
            k=4,
            filters={"type": "case_study"},
        )
        assert results["matches"][0]["evidence"]["id"] == "cs-fs-p01"
        case = await _call(client, "get_case_study", id="fs-p01")
        assert "Stripe Checkout" in case["text"]
        assert "Stripe" in (await _call(client, "list_skills"))["skills"]
        denied = await _call(
            client, "search_evidence", requirement="Python", filters={"persona": "ai_ml"}
        )
        assert denied["matches"] == []
        assert len((await client.list_resource_templates()).resourceTemplates) == 3
        for kind, evidence_type in (
            ("case", "case_study"),
            ("proposal", "past_proposal"),
            ("repo", "repo"),
        ):
            item = next(evidence for evidence in corpus if evidence.type == evidence_type)
            key = item.id.removeprefix("repo-") if kind == "repo" else item.id
            result = await client.read_resource(AnyUrl(f"portfolio://{kind}/{key}"))
            assert result.contents[0].text == item.text
        prompts = (await client.list_prompts()).prompts
        assert len(prompts) == 3
        answer = await client.get_prompt(
            "screening_answer", arguments={"question": "Do you know Stripe?"}
        )
        assert "Do you know Stripe?" in answer.messages[0].content.text
