"""Checkpoint incoming turns and require approval of exact outbound messages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from agent.config import REPO_ROOT, GigBidConfig, get_config
from agent.handoffs import ApprovalDecision, ClaimVerification
from agent.nodes.client_liaison import (
    ConversationIntent,
    InterviewSlot,
    NegotiationPolicy,
    ReplyDraft,
    check_message,
    classify_intent,
    draft_reply,
    load_message_rules,
    verify_reply,
)
from agent.nodes.fit_matcher import PortfolioEvidence
from mcp_servers.marketplace.port import ConnectsBalance, Message

ConversationStatus = Literal[
    "idle", "running", "awaiting_approval", "needs_attention", "skipped", "done"
]


class ConversationMarketplace(Protocol):
    async def get_connects_balance(self) -> ConnectsBalance: ...

    async def list_messages(self, thread_id: str) -> list[Message]: ...

    async def send_message(self, thread_id: str, body: str, *, idempotency_key: str) -> Message: ...


class ConversationInput(TypedDict):
    thread_id: str
    availability: NotRequired[list[dict[str, Any]]]


class ConversationState(ConversationInput, total=False):
    marketplace_thread_id: str
    account_id: str
    last_handled_message_id: int
    status: ConversationStatus
    incoming: dict[str, Any]
    intent: dict[str, Any] | None
    draft: dict[str, Any] | None
    draft_reference: dict[str, Any] | None
    draft_version: int
    claim_verification: list[dict[str, Any]]
    compliance_verdict: dict[str, Any] | None
    proposal_digest: str | None
    approval_status: Literal["pending", "edited", "approved", "rejected"] | None
    approval_decision: dict[str, Any] | None
    approval_response: Any
    approval_error: str | None
    outcome: dict[str, Any] | None
    errors: list[str]


class ConversationPreview(BaseModel):
    thread_id: str
    incoming: Message
    intent: ConversationIntent
    draft: ReplyDraft
    draft_version: int
    claim_verification: list[ClaimVerification]
    proposal_digest: str
    error: str | None = None


def _digest(value: object) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def _approval_digest(state: ConversationState) -> str:
    return _digest(
        [
            state["account_id"],
            state["marketplace_thread_id"],
            state["incoming"]["id"],
            state["draft_version"],
            state["draft"],
        ]
    )


def _attention(error: str) -> ConversationState:
    return {"status": "needs_attention", "errors": [error]}


def build_conversation_graph(
    *,
    marketplace: ConversationMarketplace,
    checkpointer: BaseCheckpointSaver,
    portfolio_evidence: Iterable[PortfolioEvidence] = (),
    negotiation: NegotiationPolicy | None = None,
    config: GigBidConfig | None = None,
) -> CompiledStateGraph:
    """Compile one durable review workflow per marketplace conversation."""
    if not checkpointer:
        raise ValueError("The conversation workflow requires a checkpointer.")
    settings = config if config is not None else get_config()
    rules = load_message_rules(REPO_ROOT / settings.policy_pack["rules_path"])
    evidence = tuple(portfolio_evidence)

    async def ingest(state: ConversationState) -> ConversationState:
        if state.get("status") in {"running", "awaiting_approval"}:
            raise ValueError("Resume the current conversation run before starting another.")
        account = await marketplace.get_connects_balance()
        thread_id = state["thread_id"]
        if (
            state.get("marketplace_thread_id", thread_id) != thread_id
            or state.get("account_id", account.account_id) != account.account_id
        ):
            raise ValueError("This checkpoint belongs to a different marketplace conversation.")
        messages = await marketplace.list_messages(thread_id)
        latest = max(messages, key=lambda item: item.id, default=None)
        if (
            latest is None
            or latest.sender != "client"
            or latest.id <= state.get("last_handled_message_id", 0)
        ):
            return {"status": state.get("status", "idle"), "availability": []}
        return {
            "marketplace_thread_id": thread_id,
            "account_id": account.account_id,
            "incoming": latest.model_dump(mode="json"),
            "status": "running",
            "intent": None,
            "draft": None,
            "draft_reference": None,
            "draft_version": 0,
            "claim_verification": [],
            "compliance_verdict": None,
            "proposal_digest": None,
            "approval_status": None,
            "approval_decision": None,
            "approval_response": None,
            "approval_error": None,
            "outcome": None,
            "errors": [],
        }

    async def classify(state: ConversationState) -> ConversationState:
        intent = classify_intent(state["incoming"]["body"])
        return {"intent": intent.model_dump(mode="json")}

    async def disengage(state: ConversationState) -> ConversationState:
        return {
            "status": "skipped",
            "availability": [],
            "last_handled_message_id": state["incoming"]["id"],
            "errors": [
                "Keep the conversation on-platform and disengage from the suspicious request."
            ],
        }

    async def draft(state: ConversationState) -> ConversationState:
        intent = ConversationIntent.model_validate(state["intent"])
        availability = [
            InterviewSlot.model_validate(slot) for slot in state.get("availability", [])
        ]
        reply = draft_reply(state["incoming"]["body"], intent, evidence, availability, negotiation)
        value = reply.model_dump(mode="json")
        return {"draft": value, "draft_reference": value, "draft_version": 1, "availability": []}

    async def verify_and_check(state: ConversationState) -> ConversationState:
        reply = ReplyDraft.model_validate(state["draft"])
        reference = ReplyDraft.model_validate(state["draft_reference"])
        verified_reply, verification = verify_reply(reply.body, reference, evidence)
        verdict = check_message(verified_reply.body, verification, rules)
        update: ConversationState = {
            "draft": verified_reply.model_dump(mode="json"),
            "claim_verification": [report.model_dump(mode="json") for report in verification],
            "compliance_verdict": verdict.model_dump(mode="json"),
        }
        if not verdict.allowed:
            update.update(_attention("The reply did not pass evidence and compliance checks."))
        return update

    async def prepare_review(state: ConversationState) -> ConversationState:
        return {
            "proposal_digest": _approval_digest(state),
            "approval_status": "pending",
            "approval_error": None,
            "status": "awaiting_approval",
        }

    async def approval(state: ConversationState) -> ConversationState:
        review = ConversationPreview.model_validate(
            {
                "thread_id": state["marketplace_thread_id"],
                "incoming": state["incoming"],
                "intent": state["intent"],
                "draft": state["draft"],
                "draft_version": state["draft_version"],
                "claim_verification": state["claim_verification"],
                "proposal_digest": state["proposal_digest"],
                "error": state["approval_error"],
            }
        )
        return {"approval_response": interrupt(review.model_dump(mode="json"))}

    async def validate_approval(state: ConversationState) -> ConversationState:
        try:
            result = ApprovalDecision.model_validate(state["approval_response"])
        except ValidationError as error:
            message = error.errors(include_url=False, include_input=False)[0]["msg"]
            return {"approval_error": message, "approval_response": None}
        if result.proposal_digest != state["proposal_digest"]:
            return {
                "approval_error": "Review the current reply before recording your decision.",
                "approval_response": None,
            }
        update: ConversationState = {
            "approval_response": None,
            "approval_error": None,
            "approval_decision": result.model_dump(mode="json"),
        }
        if result.action == "reject":
            update.update(
                approval_status="rejected",
                status="done",
                last_handled_message_id=state["incoming"]["id"],
            )
        elif result.action == "edit":
            replacement = ReplyDraft(body=result.text)
            update.update(
                approval_status="edited",
                status="running",
                draft=replacement.model_dump(mode="json"),
                draft_version=state["draft_version"] + 1,
                proposal_digest=None,
            )
        else:
            messages = await marketplace.list_messages(state["marketplace_thread_id"])
            if any(message.id > state["incoming"]["id"] for message in messages):
                update.update(_attention("The conversation changed. Review the latest message."))
                update.update(approval_status=None, approval_decision=None, proposal_digest=None)
            else:
                update.update(approval_status="approved", status="running")
        return update

    def after_approval(state: ConversationState) -> str:
        if state["approval_error"]:
            return "approval"
        if state["approval_status"] == "approved":
            return "send"
        if state["approval_status"] == "edited":
            return "verify_and_check"
        return END

    async def send(state: ConversationState) -> ConversationState:
        result = ApprovalDecision.model_validate(state["approval_decision"])
        if (
            state["approval_status"] != "approved"
            or result.action != "approve"
            or result.proposal_digest != state["proposal_digest"]
            or state["proposal_digest"] != _approval_digest(state)
            or not state["compliance_verdict"]["allowed"]
        ):
            return _attention("The reply must match the approved and verified preview.")
        account = await marketplace.get_connects_balance()
        if account.account_id != state["account_id"]:
            return _attention("The approval belongs to a different marketplace account.")
        message_id = state["incoming"]["id"]
        key = "gigbid:reply:" + _digest(
            [state["account_id"], state["marketplace_thread_id"], message_id]
        )
        receipt = await marketplace.send_message(
            state["marketplace_thread_id"], state["draft"]["body"], idempotency_key=key
        )
        return {
            "outcome": receipt.model_dump(mode="json"),
            "status": "done",
            "last_handled_message_id": message_id,
        }

    routes = {
        "question": "answer_question",
        "negotiation": "prepare_counteroffer",
        "interview_request": "suggest_interview_times",
        "scam_signal": "disengage",
    }
    graph = StateGraph(ConversationState, input_schema=ConversationInput)
    for node in (
        ingest,
        classify,
        disengage,
        verify_and_check,
        prepare_review,
        approval,
        validate_approval,
        send,
    ):
        graph.add_node(node)
    for name in ("answer_question", "prepare_counteroffer", "suggest_interview_times"):
        graph.add_node(name, draft)
        graph.add_edge(name, "verify_and_check")
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges(
        "ingest",
        lambda state: "classify" if state["status"] == "running" else END,
        ["classify", END],
    )
    graph.add_conditional_edges(
        "classify",
        lambda state: routes[state["intent"]["category"]],
        list(routes.values()),
    )
    graph.add_edge("disengage", END)
    graph.add_conditional_edges(
        "verify_and_check",
        lambda state: "prepare_review" if state["status"] == "running" else END,
        ["prepare_review", END],
    )
    graph.add_edge("prepare_review", "approval")
    graph.add_edge("approval", "validate_approval")
    graph.add_conditional_edges(
        "validate_approval", after_approval, ["approval", "send", "verify_and_check", END]
    )
    graph.add_edge("send", END)
    return graph.compile(checkpointer=checkpointer, name="conversation")
