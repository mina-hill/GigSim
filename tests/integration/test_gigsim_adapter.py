from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from gigsim.app import app
from gigsim.db import get_engine
from gigsim.settings import get_settings
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.port import Job, MarketplacePort, ProposalInput

WORLD = Path(__file__).resolve().parents[2] / "gigsim" / "fixtures" / "mini_world"
ADMIN = {"X-Admin-Token": "adapter-test-admin"}
ACCOUNT_TOKEN = "adapter-test-account"
LETTER = (
    "Your support team needs a Python retrieval pipeline that cites the exact policy page "
    "and refuses when the PDFs are silent. I built a RAG assistant over 3,400 PDF pages with "
    "hybrid search and page-level citations. I evaluated it on 200 questions: 91% of answers "
    "cited the right page. I would ingest your 2,000 policies, add a grounded-answer check, "
    "and deploy on your AWS account with a small evaluation set your team can extend. "
    "The first milestone covers ingestion and an evaluation report."
)


@pytest.fixture
async def marketplace(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[MarketplacePort, httpx.AsyncClient]]:
    monkeypatch.setenv("GIGSIM_DB_URL", "sqlite:///:memory:")
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    monkeypatch.setenv("GIGSIM_LATENCY_MS_MAX", "0")
    monkeypatch.setenv("GIGSIM_RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("GIGSIM_CLIENT_MODE", "rules")
    monkeypatch.setenv("GIGSIM_POLICY_VERSION", "v1")
    monkeypatch.setenv("GIGSIM_API_TOKENS", f"{ACCOUNT_TOKEN}:persona_ai_ml")
    monkeypatch.setenv("GIGSIM_ADMIN_TOKEN", ADMIN["X-Admin-Token"])
    get_settings.cache_clear()
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://sim.test"
            ) as client,
        ):
            seed = await client.post("/admin/seed", json={"world_path": str(WORLD)}, headers=ADMIN)
            seed.raise_for_status()
            yield GigSimAdapter(client, token=ACCOUNT_TOKEN), client
    finally:
        get_engine().dispose()
        get_settings.cache_clear()


async def test_reads_jobs_history_and_connects_without_writes(marketplace):
    adapter, _ = marketplace
    page = await adapter.search_jobs(domain="data_analysis", limit=1)
    assert page.next_cursor is not None
    next_page = await adapter.search_jobs(domain="data_analysis", limit=1, cursor=page.next_cursor)
    assert next_page.next_cursor is None
    assert {item.job_id for item in page.items + next_page.items} == {"job_data_pbi", "job_inj_etl"}

    job = await adapter.get_job("job_ai_rag")
    assert isinstance(job, Job)
    assert job.payment_type == "fixed"
    history = await adapter.get_client_history(job.client.client_id)
    assert history.client.client_id == job.client.client_id
    stat, *_ = await adapter.get_market_stats(domain="data_analysis")
    assert stat.p25 <= stat.p50 <= stat.p75

    quote = await adapter.quote_connects(job.job_id)
    assert quote.connects_cost == job.connects_cost == 16
    assert quote.balance_after == quote.balance - quote.connects_cost
    assert (await adapter.get_connects_balance()).balance == quote.balance
    assert await adapter.list_proposals() == []


async def test_retries_after_lost_submit_response_only_spend_connects_once(marketplace):
    adapter, client = marketplace
    before = await adapter.get_connects_balance()
    proposal = ProposalInput(
        job_id="job_ai_rag",
        cover_letter=LETTER,
        bid_amount=4200,
        duration_days=21,
        screening_answers=["I evaluated the Python RAG assistant on 200 questions."],
    )
    draft = await adapter.create_proposal_draft(proposal, idempotency_key="test-draft")
    assert draft.status == "draft"
    assert await adapter.create_proposal_draft(proposal, idempotency_key="test-draft") == draft
    assert (await adapter.get_connects_balance()).balance == before.balance

    faults = await client.put("/admin/faults", json={"post_commit_error_rate": 1}, headers=ADMIN)
    faults.raise_for_status()
    with pytest.raises(httpx.HTTPStatusError) as lost:
        await adapter.submit_proposal(draft.proposal_id, idempotency_key="test-submit")
    assert lost.value.response.status_code == 502

    faults = await client.put("/admin/faults", json={"post_commit_error_rate": 0}, headers=ADMIN)
    faults.raise_for_status()
    submitted = await adapter.submit_proposal(draft.proposal_id, idempotency_key="test-submit")
    assert submitted.status == "submitted"
    assert await adapter.get_proposal(draft.proposal_id) == submitted
    assert await adapter.list_proposals() == [submitted]
    assert (await adapter.get_connects_balance()).balance == before.balance - draft.connects_cost
    events = await adapter.list_events()
    assert [event.type for event in events].count("proposal_submitted") == 1
    assert await adapter.list_events(since=events[-1].id) == []

    tick = await client.post("/admin/tick", json={"n": 3}, headers=ADMIN)
    tick.raise_for_status()
    (thread,) = await adapter.list_threads()
    (first,) = await adapter.list_messages(thread.thread_id)
    assert first.sender == "client"
    reply = "I can meet on Tuesday at 10am (UTC+5) to discuss the evaluation."
    message = await adapter.send_message(thread.thread_id, reply, idempotency_key="test-reply")
    assert message.sender == "freelancer"
    assert await adapter.send_message(thread.thread_id, reply, idempotency_key="test-reply") == (
        message
    )
    assert await adapter.list_messages(thread.thread_id) == [first, message]


async def test_adapter_preserves_upstream_errors_and_rejects_empty_keys(marketplace):
    adapter, client = marketplace
    with pytest.raises(httpx.HTTPStatusError) as unauthorized:
        await GigSimAdapter(client, token="invalid-token").get_connects_balance()
    assert unauthorized.value.response.status_code == 401

    with pytest.raises(ValueError, match="idempotency key"):
        await adapter.submit_proposal("prop_missing", idempotency_key="")
    with pytest.raises(ValueError, match="one path segment"):
        await adapter.get_job("../admin/state")
