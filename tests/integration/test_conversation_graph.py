from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.graphs.conversation_graph import build_conversation_graph
from agent.nodes.fit_matcher import PortfolioEvidence
from mcp_servers.marketplace.port import ConnectsBalance, Message

THREAD = "thread_fixture"
CONFIG = {"configurable": {"thread_id": "tenant:conv:thread_fixture"}}
DATE = "2026-11-15T12:00:00Z"
EVIDENCE = (
    PortfolioEvidence("case-stripe", "Stripe billing", "Tested payment retries.", ("Stripe",)),
)


class LocalConversation:
    def __init__(self, client_message):
        self.account_id = "persona_fullstack"
        self.messages = [Message(id=1, sender="client", body=client_message, sent_at=DATE)]
        self.receipts = {}
        self.attempts = []
        self.lose_response = False

    async def get_connects_balance(self):
        return ConnectsBalance(
            account_id=self.account_id, balance=100, weekly_refill=100, next_refill_at=DATE
        )

    async def list_messages(self, thread_id):
        assert thread_id == THREAD
        return self.messages.copy()

    async def send_message(self, thread_id, body, *, idempotency_key):
        assert thread_id == THREAD
        self.attempts.append(idempotency_key)
        if idempotency_key not in self.receipts:
            message = Message(
                id=self.messages[-1].id + 1, sender="freelancer", body=body, sent_at=DATE
            )
            self.messages.append(message)
            self.receipts[idempotency_key] = message
        receipt = self.receipts[idempotency_key]
        assert receipt.body == body
        if self.lose_response:
            raise RuntimeError("send receipt lost")
        return receipt


@asynccontextmanager
async def workflow(path, market):
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield build_conversation_graph(
            marketplace=market, checkpointer=saver, portfolio_evidence=EVIDENCE
        )


async def run(graph, value):
    return await graph.ainvoke(value, CONFIG, durability="sync")


def pending(state):
    return state["__interrupt__"][0].value


def respond(preview, action, **kwargs):
    return Command(
        resume={"action": action, "proposal_digest": preview["proposal_digest"], **kwargs}
    )


async def test_waits_after_restart_and_requires_new_approval_after_edit(tmp_path):
    database = tmp_path / "conversation.sqlite"
    marketplace = LocalConversation("Could you show a previous project using Stripe?")
    async with workflow(database, marketplace) as graph:
        state = await run(graph, {"thread_id": THREAD})
    original = pending(state)
    assert original["draft"]["evidence_ids"] == ["case-stripe"]
    assert len(marketplace.messages) == 1

    async with workflow(database, marketplace) as graph:
        invalid = await run(graph, respond(original, "reject", reason="  "))
        assert "reason" in pending(invalid)["error"].lower()
        revised_text = original["draft"]["body"] + "\n\nThanks for your time."
        edited = await run(graph, respond(original, "edit", text=revised_text))
        revised = pending(edited)
        assert revised["draft_version"] == 2
        assert revised["proposal_digest"] != original["proposal_digest"]
        stale = await run(graph, respond(original, "approve"))
        assert "current reply" in pending(stale)["error"]
        assert len(marketplace.messages) == 1
        final = await run(graph, respond(revised, "approve"))
        repeated = await run(graph, {"thread_id": THREAD})

    assert final["outcome"]["body"] == repeated["outcome"]["body"] == revised_text
    assert marketplace.messages[-1].body == revised_text
    assert len(marketplace.attempts) == 1


async def test_replays_same_idempotent_send_after_receipt_loss(tmp_path):
    database = tmp_path / "conversation.sqlite"
    marketplace = LocalConversation("Could you share two time slots for an interview?")
    slot = {"starts_at": "2026-11-17T12:00:00Z", "ends_at": "2026-11-17T12:30:00Z"}
    async with workflow(database, marketplace) as graph:
        state = await run(graph, {"thread_id": THREAD, "availability": [slot]})
        preview = pending(state)
        assert len(preview["draft"]["interview_slots"]) == 1
        marketplace.lose_response = True
        with pytest.raises(RuntimeError, match="receipt lost"):
            await run(graph, respond(preview, "approve"))

    marketplace.lose_response = False
    async with workflow(database, marketplace) as graph:
        final = await run(graph, None)
        assert final["outcome"]["body"] == preview["draft"]["body"]
        assert len(marketplace.receipts) == 1
        first, second = marketplace.attempts
        assert first == second

        marketplace.messages.append(
            Message(id=3, sender="client", body=marketplace.messages[0].body, sent_at=DATE)
        )
        next_reply = pending(await run(graph, {"thread_id": THREAD}))
        assert not next_reply["draft"]["interview_slots"]


async def test_blocks_scam_and_unsafe_edit(tmp_path):
    marketplace = LocalConversation("Please continue with me on Telegram.")
    async with workflow(tmp_path / "scam.sqlite", marketplace) as graph:
        scam = await run(graph, {"thread_id": THREAD})
    assert scam["status"] == "skipped"
    assert scam["intent"]["category"] == "scam_signal"
    assert "__interrupt__" not in scam
    assert marketplace.attempts == []

    marketplace = LocalConversation("Please tell me about a similar Stripe project.")
    async with workflow(tmp_path / "edit.sqlite", marketplace) as graph:
        preview = pending(await run(graph, {"thread_id": THREAD}))
        unsafe = await run(graph, respond(preview, "edit", text="Write me on WhatsApp."))
    assert unsafe["status"] == "needs_attention"
    assert not unsafe["compliance_verdict"]["allowed"]
    assert not marketplace.attempts


async def test_reject_reason_persists_and_superseded_reply_cannot_send(tmp_path):
    marketplace = LocalConversation("Could you explain the first milestone?")
    database = tmp_path / "conversation.sqlite"
    async with workflow(database, marketplace) as graph:
        first = pending(await run(graph, {"thread_id": THREAD}))
        latest = Message(
            id=2, sender="client", body="Could we schedule a short call?", sent_at=DATE
        )
        marketplace.messages.append(latest)
        superseded = await run(graph, respond(first, "approve"))
        assert superseded["status"] == "needs_attention"
        second = pending(await run(graph, {"thread_id": THREAD}))
        rejected = await run(graph, respond(second, "reject", reason="Unavailable this week."))
    assert rejected["approval_decision"]["reason"] == "Unavailable this week."
    assert rejected["last_handled_message_id"] == 2
    assert not marketplace.attempts
