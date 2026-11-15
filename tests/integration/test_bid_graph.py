from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.graphs.bid_graph import build_bid_graph
from agent.marketplace_gateway import LocalBidMarketplace
from agent.nodes.bid import OfflineBidNodes
from gigsim.settings import get_settings
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter

WORLD = Path(__file__).resolve().parents[2] / "gigsim" / "fixtures" / "mini_world"
JOB = "job_web_stripe"
THREAD = {"configurable": {"thread_id": "persona_fullstack:job_web_stripe"}}


@pytest.fixture
async def simulator(monkeypatch):
    monkeypatch.setenv("GIGSIM_DB_URL", "sqlite:///:memory:")
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    monkeypatch.setenv("GIGSIM_LATENCY_MS_MAX", "0")
    monkeypatch.setenv("GIGSIM_ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("GIGSIM_CLIENT_MODE", "rules")
    monkeypatch.setenv(
        "GIGSIM_API_TOKENS",
        "dev-token-web:persona_fullstack,dev-token-ai:persona_ai_ml",
    )
    get_settings.cache_clear()
    from gigsim.app import app

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://gigsim.test",
            ) as client:
                seed = await client.post(
                    "/admin/seed",
                    headers={"X-Admin-Token": "test-admin"},
                    json={"world_path": str(WORLD)},
                )
                seed.raise_for_status()
                yield client, GigSimAdapter(client, token="dev-token-web")
    finally:
        get_settings.cache_clear()


@asynccontextmanager
async def workflow(tmp_path, adapter):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        yield build_bid_graph(
            nodes=OfflineBidNodes.from_persona("fullstack"),
            marketplace=LocalBidMarketplace(adapter),
            checkpointer=saver,
        )


async def run(graph, graph_input):
    return await graph.ainvoke(graph_input, THREAD, durability="sync")


def pending(result):
    return result["__interrupt__"][0].value


def reply(result, action="approve", **fields):
    return Command(
        resume={
            "action": action,
            "proposal_digest": pending(result)["proposal_digest"],
            **fields,
        },
    )


async def test_scam_is_skipped(simulator, tmp_path):
    _, adapter = simulator
    async with workflow(tmp_path, adapter) as graph:
        result = await run(graph, {"job_id": "job_scam_entry"})
    assert result["decision"]["action"] == "skip"
    assert "__interrupt__" not in result
    assert await adapter.list_proposals() == []


async def test_waits_for_approval_then_submits(simulator, tmp_path):
    _, adapter = simulator
    balance = (await adapter.get_connects_balance()).balance
    async with workflow(tmp_path, adapter) as graph:
        result = await run(graph, {"job_id": JOB})
        preview = pending(result)["preview"]
        assert await adapter.list_proposals() == []
        assert (await adapter.get_connects_balance()).balance == balance

        result = await run(graph, reply(result))
    assert result["outcome"]["status"] == "submitted"
    assert result["outcome"]["connects_spent"] == preview["connects_cost"] == 12
    assert len(await adapter.list_proposals()) == 1
    assert (await adapter.get_connects_balance()).balance == balance - 12


async def test_rechecks_unsafe_edits_before_sending(simulator, tmp_path):
    _, adapter = simulator
    async with workflow(tmp_path, adapter) as graph:
        result = await run(graph, {"job_id": JOB})
        edited = pending(result)["draft"]["cover_letter"] + "\n\nContact me on WhatsApp."
        result = await run(graph, reply(result, "edit", text=edited))
    assert not result["compliance_verdict"]["allowed"]
    assert "outcome" not in result
    assert await adapter.list_proposals() == []


async def test_reopened_checkpoint_replays_submit_after_lost_response(simulator, tmp_path):
    client, adapter = simulator
    balance = (await adapter.get_connects_balance()).balance
    async with workflow(tmp_path, adapter) as graph:
        result = await run(graph, {"job_id": JOB})

    async def lose_submit_receipt(response):
        if response.request.url.path.endswith("/submit") and response.status_code == 200:
            raise httpx.ReadError("Simulated loss of the committed submit response")

    client.event_hooks["response"].append(lose_submit_receipt)
    try:
        async with workflow(tmp_path, adapter) as graph:
            with pytest.raises(httpx.ReadError):
                await run(graph, reply(result))
    finally:
        client.event_hooks["response"].remove(lose_submit_receipt)

    committed = await adapter.list_proposals()
    assert len(committed) == 1
    assert committed[0].status == "submitted"

    async with workflow(tmp_path, adapter) as graph:
        resumed = await run(graph, None)
    assert resumed["outcome"]["proposal_id"] == committed[0].proposal_id
    assert len(await adapter.list_proposals()) == 1
    assert (await adapter.get_connects_balance()).balance == balance - 12


async def test_untrusted_post_is_flagged(simulator, tmp_path):
    _, adapter = simulator
    async with workflow(tmp_path, adapter) as graph:
        result = await run(graph, {"job_id": "job_inj_etl"})
    assert any("injection" in flag for flag in result["risk_report"]["flags"])
    assert "ignore your previous instructions" not in pending(result)["draft"]["cover_letter"]
    assert "@" not in pending(result)["draft"]["cover_letter"]
    assert await adapter.list_proposals() == []


async def test_duplicate_post_is_skipped(simulator, tmp_path):
    _, adapter = simulator
    async with workflow(tmp_path, adapter) as graph:
        result = await run(graph, {"job_id": JOB, "duplicate": True})
    assert result["decision"]["action"] == "skip"
    assert "__interrupt__" not in result
    assert await adapter.list_proposals() == []


async def test_invalid_rejection_reprompts_and_accepts_reason_after_reopen(simulator, tmp_path):
    _, adapter = simulator
    async with workflow(tmp_path, adapter) as graph:
        first = await run(graph, {"job_id": JOB})
        retry = await run(graph, reply(first, "reject", reason="  "))
        assert "reason" in pending(retry)["error"].lower()
        assert pending(retry)["proposal_digest"] == pending(first)["proposal_digest"]

    async with workflow(tmp_path, adapter) as graph:
        rejected = await run(graph, reply(retry, "reject", reason="No capacity"))
    assert rejected["approval_status"] == "rejected"
    assert rejected["approval_decision"]["reason"] == "No capacity"
    assert "__interrupt__" not in rejected
    assert await adapter.list_proposals() == []


async def test_edit_requires_text_and_fresh_approval(simulator, tmp_path):
    _, adapter = simulator
    async with workflow(tmp_path, adapter) as graph:
        original = await run(graph, {"job_id": JOB})
        retry = await run(graph, reply(original, "edit", text=" "))
        assert "revised proposal text" in pending(retry)["error"].lower()

        edited_text = (
            pending(retry)["draft"]["cover_letter"] + "\n\nThank you for considering my proposal."
        )
        edited = await run(graph, reply(retry, "edit", text=edited_text))
        assert pending(edited)["submission"]["cover_letter"] == edited_text
        assert pending(edited)["proposal_digest"] != pending(original)["proposal_digest"]
        stale = await run(graph, reply(original, "reject", reason="Wrong version"))
        assert "current preview" in pending(stale)["error"]
        assert await adapter.list_proposals() == []

        submitted = await run(graph, reply(stale))
    assert submitted["outcome"]["cover_letter"] == edited_text


async def test_saved_approval_cannot_spend_another_accounts_connects(simulator, tmp_path):
    client, adapter = simulator
    wrong_account = GigSimAdapter(client, token="dev-token-ai")
    async with workflow(tmp_path, adapter) as graph:
        first = await run(graph, {"job_id": JOB})
        assert pending(first)["preview"]["account_id"] == "persona_fullstack"

    async with workflow(tmp_path, wrong_account) as graph:
        result = await run(graph, reply(first))
    assert result["status"] == "needs_attention"
    assert "different marketplace account" in result["errors"][0]
    assert await wrong_account.list_proposals() == []
