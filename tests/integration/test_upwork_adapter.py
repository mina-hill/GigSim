"""The Upwork adapter maps Upwork's tool responses onto the marketplace port.

The payloads are synthetic and follow the shapes in Upwork's tool reference. Writes are
checked to stop at Upwork's preview: confirm_preview and send are never called.
"""

import contextlib
import json

import pytest
from cryptography.fernet import Fernet
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_servers.audit import AuditLog
from mcp_servers.marketplace.adapters.upwork_auth import EncryptedTokenStorage, upwork_session
from mcp_servers.marketplace.adapters.upwork_live import (
    UpworkAdapter,
    applicants_from_tier,
    parse_budget,
)
from mcp_servers.marketplace.port import MarketplaceError, ProposalInput
from mcp_servers.marketplace.server import create_marketplace_server
from tests.support.upwork_mock import MockUpwork

ROW_HOURLY = {
    "id": "1001",
    "title": "Build a RAG chatbot over PDFs",
    "description_snippet": "<untrusted_participant_content>\nNeed retrieval with citations "
    "using Python and an LLM.\n</untrusted_participant_content>",
    "job_type": "hourly",
    "budget": "$30.00–$45.00/hr",
    "duration": "1 to 3 months",
    "experience_level": "Expert",
    "proposals_tier": "5 to 10",
    "skills": ["Python", "LLM", "RAG"],
    "published_date": "2026-11-15T10:00:00Z",
    "client": {
        "country": "Canada",
        "verification_status": "VERIFIED",
        "total_spent": "$12,400.50",
        "rating": 4.8,
        "total_reviews": 9,
    },
}
ROW_FIXED = {
    "id": "1002",
    "title": "Fix a Next.js checkout page",
    "description_snippet": "Stripe checkout fails on mobile.",
    "job_type": "fixed",
    "budget": "$500.00",
    "proposals_tier": "Fewer than 5",
    "skills": ["Next.js", "Stripe"],
    "client": {"country": "USA", "verification_status": "NOT_VERIFIED"},
}
DETAILS = {
    "status": "ok",
    "applied": False,
    "connects_cost": 14,
    "connects_balance": 120,
    "client_record": {
        "spend_total": "$15,000.00",
        "hire_rate_percent": 60,
        "feedback_count": 10,
        "feedback_score": 4.9,
        "jobs_posted": 20,
        "open_jobs": 3,
    },
    "client_work_history": {
        "closed": [{"title": "Earlier chatbot", "feedback_score": 5, "feedback_comment": "Great"}]
    },
    "data": {
        "marketplaceJobPosting": {
            "id": "1001",
            "content": {
                "title": "Build a RAG chatbot over PDFs",
                "description": "<untrusted_participant_content>Full description: retrieval, "
                "citations, evaluation.</untrusted_participant_content>",
            },
            "contractTerms": {
                "contractType": "HOURLY",
                "experienceLevel": "EXPERT",
                "hourlyContractTerms": {"hourlyBudgetMin": 30, "hourlyBudgetMax": 45},
            },
            "classification": {"category": {"preferredLabel": "Web, Mobile & Software Dev"}},
        }
    },
}


def mock_upwork(**overrides):
    def find_jobs(args):
        if args["action"] == "get":
            return DETAILS
        if args["params"].get("cursor"):
            return {"status": "ok", "jobs": [ROW_FIXED], "hasMore": False}
        return {
            "status": "ok",
            "jobs": [ROW_HOURLY, ROW_FIXED],
            "hasMore": True,
            "next_cursor": "o:2",
        }

    def proposals(args):
        if args["action"] == "invitations":
            return {"status": "ok", "data": {"vendorProposals": {"edges": []}}}
        return {
            "status": "ok",
            "data": {
                "vendorProposals": {
                    "edges": [
                        {
                            "node": {
                                "id": "p-9",
                                "status": "Accepted",
                                "status_label": "Submitted",
                                "coverLetter": "Hello",
                                "terms": {"chargeRate": {"rawValue": "40"}},
                                "marketplaceJobPosting": {"id": "1001"},
                            }
                        }
                    ]
                }
            },
        }

    def manage(args):
        assert args["action"] == "create"
        params = args["params"]
        preview = {
            "status": "ok",
            "preview_id": "pv_77",
            "type": "proposal",
            "connects_cost": 14,
            "connects_balance": 120,
        }
        if "answers" not in params:
            preview["screening_questions"] = [{"question": "Which vector store?"}]
        return preview

    tools = {
        "upwork__find_jobs": find_jobs,
        "upwork__get_profile": lambda _: {"status": "ok", "balance": {"connectsBalance": 120}},
        "upwork__list_freelancer_proposals": proposals,
        "upwork__manage_proposals": manage,
        "upwork__get_messages": lambda a: {
            "status": "ok",
            "data": {
                "rooms": [
                    {
                        "id": "room_1",
                        "awaiting_reply_from": "you",
                        "latestStory": {"createdDateTime": "2026-11-15T09:00:00Z"},
                    }
                ]
            }
            if a["action"] == "list_rooms"
            else {
                "messages": [
                    {
                        "message": "Can you start Monday?",
                        "from_side": "client",
                        "createdDateTime": "2026-11-15T09:00:00Z",
                    },
                    {
                        "message": "<untrusted_participant_content>Hi! Saw your proposal."
                        "</untrusted_participant_content>",
                        "from_side": "client",
                        "createdDateTime": "2026-11-15T08:00:00Z",
                    },
                    {
                        "message": "Thanks for the invite",
                        "from_self": True,
                        "createdDateTime": "2026-11-15T07:00:00Z",
                    },
                ]
            },
        },
        **overrides,
    }
    return MockUpwork(tools=tools)


@contextlib.asynccontextmanager
async def adapter_for(tmp_path, mock, **kwargs):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    storage = EncryptedTokenStorage(tmp_path, "mina", Fernet(Fernet.generate_key()))
    async with upwork_session(
        storage,
        interactive=True,
        port=port,
        timeout=10,
        launch_browser=mock.browser,
        httpx_client_factory=mock.client_factory(),
    ) as session:
        yield UpworkAdapter(session, "org-1", **kwargs)


def test_budget_and_applicant_parsing():
    assert parse_budget("$10.00–$15.00/hr", "hourly")["hourly_max"] == 15.0
    assert parse_budget("$25.00+/hr", "hourly")["hourly_min"] == 25.0
    assert parse_budget("up to $30.00/hr", "hourly")["hourly_max"] == 30.0
    assert parse_budget("$1,500.00", "fixed")["budget_max"] == 1500.0
    assert applicants_from_tier({"proposals_tier": "20 to 50"}) == 20
    assert applicants_from_tier({"proposals_tier": "Fewer than 5"}) == 0
    assert applicants_from_tier({"proposal_count": 7}) == 7


async def test_reads_map_onto_the_port(tmp_path):
    mock = mock_upwork()
    async with adapter_for(tmp_path, mock, min_interval=0) as upwork:
        page = await upwork.search_jobs(q="rag chatbot", limit=50)
        hourly, fixed = page.items
        assert hourly.payment_type == "hourly" and hourly.hourly_min == 30.0
        assert hourly.domain == "ai_ml" and hourly.applicants == 5
        assert "untrusted_participant_content" not in hourly.description
        assert hourly.client.payment_verified and hourly.client.total_spent == 12400.5
        assert fixed.budget_max == 500.0 and not fixed.client.payment_verified
        assert page.next_cursor == 1
        assert [j.job_id for j in (await upwork.search_jobs(q="rag", cursor=1)).items] == ["1002"]

        job = await upwork.get_job("1001")
        assert job.connects_cost == 14 and job.description.startswith("Full description")
        assert job.client.hire_rate == 0.6 and job.client.rating == 4.9
        assert job.client.country == "Canada"  # kept from the search row

        history = await upwork.get_client_history(job.client.client_id)
        assert history.open_jobs == 3 and history.past_contracts[0].review == "Great"
        quote = await upwork.quote_connects("1001")
        assert (quote.connects_cost, quote.balance_after) == (14, 106)
        assert (await upwork.get_connects_balance()).balance == 120
        assert await upwork.get_market_stats() == []
        assert (await upwork.list_proposals())[0].bid_amount == 40.0

        assert (await upwork.list_threads())[0].thread_id == "room_1"
        messages = await upwork.list_messages("room_1")
        assert [m.sender for m in messages] == ["freelancer", "client", "client"]
        assert messages[1].body == "Hi! Saw your proposal."
    sent = [args["params"] for name, args in mock.calls if name == "upwork__find_jobs"]
    assert sent[0] == {"limit": 10, "query": "rag chatbot"}


async def test_without_a_query_it_reads_the_freelancers_newest_matches(tmp_path):
    mock = mock_upwork()
    async with adapter_for(tmp_path, mock, min_interval=0) as upwork:
        await upwork.search_jobs(posted_after="2026-11-14")
    assert mock.calls[0][1]["action"] == "smart_search"
    assert mock.calls[0][1]["params"] == {
        "limit": 10,
        "mode": "most_recent",
        "from_date": "2026-11-14",
    }


async def test_a_draft_stops_at_upworks_preview_and_nothing_is_sent(tmp_path):
    mock = mock_upwork()
    proposal = ProposalInput(
        job_id="1001",
        cover_letter="I built a cited RAG bot.",
        bid_amount=40,
        bid_type="hourly",
        screening_answers=["pgvector, with BM25 fusion."],
    )
    async with adapter_for(tmp_path, mock, min_interval=0) as upwork:
        draft = await upwork.create_proposal_draft(proposal, idempotency_key="k1")
        again = await upwork.create_proposal_draft(proposal, idempotency_key="k1")
        assert draft == again and draft.status == "draft" and draft.connects_cost == 14
        assert (await upwork.get_proposal("pv_77")).stage == "upwork_preview"
        with pytest.raises(MarketplaceError, match="confirm it yourself"):
            await upwork.submit_proposal("pv_77", idempotency_key="k2")
        with pytest.raises(MarketplaceError, match="does not send"):
            await upwork.send_message("room_1", "Hello", idempotency_key="k3")
    creates = [args for name, args in mock.calls if name == "upwork__manage_proposals"]
    assert len(creates) == 2  # the second carries the screening answers
    assert creates[1]["params"]["answers"] == [
        {"question": "Which vector store?", "answer": "pgvector, with BM25 fusion."}
    ]
    called = {name for name, _ in mock.calls}
    assert "upwork__confirm_preview" not in called and "upwork__send_message" not in called


async def test_an_invitation_or_missing_answers_stop_the_draft(tmp_path):
    invited = mock_upwork(
        upwork__list_freelancer_proposals=lambda a: {
            "status": "ok",
            "data": {"vendorProposals": {"edges": [{"node": {"id": "inv-1"}}]}},
        }
    )
    proposal = ProposalInput(job_id="1001", cover_letter="Hello", bid_amount=40)
    async with adapter_for(tmp_path, invited, min_interval=0) as upwork:
        with pytest.raises(MarketplaceError, match="invitation"):
            await upwork.create_proposal_draft(proposal, idempotency_key="k")
    async with adapter_for(tmp_path / "b", mock_upwork(), min_interval=0) as upwork:
        with pytest.raises(MarketplaceError, match="screening questions"):
            await upwork.create_proposal_draft(proposal, idempotency_key="k")
        with pytest.raises(MarketplaceError, match="5,000"):
            await upwork.create_proposal_draft(
                proposal.model_copy(update={"cover_letter": "x" * 5001}), idempotency_key="k2"
            )


async def test_calls_are_paced(tmp_path):
    now = [100.0]
    waits = []

    async def sleep(seconds):
        waits.append(round(seconds, 2))
        now[0] += seconds

    async with adapter_for(
        tmp_path, mock_upwork(), min_interval=2.0, clock=lambda: now[0], sleep=sleep
    ) as upwork:
        await upwork.get_connects_balance()
        now[0] += 0.5
        await upwork.get_connects_balance()
    assert waits == [1.5]


async def test_upwork_errors_become_readable_tool_errors(tmp_path):
    mock = mock_upwork(upwork__get_profile=lambda _: {"status": "error", "message": "Try later"})
    async with adapter_for(tmp_path, mock, min_interval=0) as upwork:
        server = create_marketplace_server(upwork, AuditLog(tmp_path / "audit.sqlite3"))
        async with create_connected_server_and_client_session(server) as client:
            balance = await client.call_tool("get_connects_balance", {})
            assert balance.isError and "Try later" in balance.content[0].text
            submit = await client.call_tool(
                "submit_proposal",
                {"proposal_id": "pv_1", "idempotency_key": "k", "dry_run": False},
            )
            assert submit.isError
    assert json.dumps(mock.calls).count("confirm_preview") == 0
