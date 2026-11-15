"""Durable bid preparation. Only the submit node can write to the marketplace."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt
from pydantic import ValidationError

from agent.config import GigBidConfig, get_config
from agent.handoffs import (
    ApprovalDecision,
    BidDecision,
    ClaimVerification,
    ComplianceVerdict,
    Critique,
    FitReport,
    JobSpec,
    PriceQuote,
    ProposalDraft,
    RiskReport,
)
from agent.marketplace_gateway import BidMarketplace, BidPreview, ConnectsQuoteChanged
from agent.state import BidGraphInput, GigBidState
from mcp_servers.marketplace.port import ProposalInput


class BidNodeServices(Protocol):
    """Analysis and drafting services with no marketplace write capability."""

    async def analyze(self, job: dict[str, Any]) -> JobSpec: ...
    async def assess_risk(self, job: dict[str, Any]) -> RiskReport: ...
    async def match_fit(
        self, job: dict[str, Any], *, portfolio_items: list[dict[str, Any]] | None = None
    ) -> FitReport: ...
    async def price(self, state: GigBidState) -> PriceQuote: ...
    async def write(self, state: GigBidState) -> ProposalDraft: ...
    async def verify(self, state: GigBidState) -> list[ClaimVerification]: ...
    async def critique(self, state: GigBidState) -> Critique: ...
    async def check_compliance(self, state: GigBidState) -> ComplianceVerdict: ...


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _attention(message: str) -> GigBidState:
    return {"status": "needs_attention", "errors": [message], "steps": 1}


def _verified(state: GigBidState) -> bool:
    return all(claim["supported"] for claim in state["claim_verification"])


def build_bid_graph(
    *,
    nodes: BidNodeServices,
    marketplace: BidMarketplace,
    checkpointer: BaseCheckpointSaver,
    config: GigBidConfig | None = None,
) -> CompiledStateGraph:
    """Compile the workflow. Start each bid in its own checkpoint thread."""
    if not checkpointer:
        raise ValueError("The bid workflow requires a checkpointer.")

    settings = config if config is not None else get_config()
    minimum_fit = float(settings.thresholds["minimum_fit_score"])
    maximum_risk = float(settings.thresholds["maximum_risk_score"])
    watch_minimum, watch_maximum = settings.thresholds["watch_fit_band"]
    weekly_connects = int(settings.connects["default_weekly_budget"])
    max_revisions = min(int(settings.budgets["max_revisions"]), 2)
    max_steps = int(settings.budgets["max_graph_steps"])

    async def ingest_job(state: GigBidState) -> GigBidState:
        if "status" in state:
            raise ValueError("Use a new checkpoint thread to start another bid.")
        job = state.get("job")
        job_id = state.get("job_id")
        if job is None:
            if not job_id:
                raise ValueError("A job or job_id is required.")
            job = (await marketplace.get_job(job_id)).model_dump(mode="json")
        if not isinstance(job.get("job_id"), str) or not job["job_id"].strip():
            raise ValueError("The job must have an id.")
        if job_id is not None and job_id != job["job_id"]:
            raise ValueError("The job id does not match the requested job.")
        return {
            "job": job,
            "job_id": job["job_id"],
            "status": "running",
            "draft_version": 0,
            "revisions": 0,
            "steps": 1,
        }

    async def reject_duplicate(state: GigBidState) -> GigBidState:
        decision = BidDecision(action="skip", rule_ids=["duplicate_posting"])
        return {
            "decision": decision.model_dump(mode="json"),
            "status": "skipped",
            "steps": 1,
        }

    def fan_out(state: GigBidState) -> str | list[Send]:
        if state.get("duplicate") or state["job"].get("duplicate_of"):
            return "reject_duplicate"
        job = {"job": state["job"]}
        return [
            Send("job_analyst", job),
            Send("risk_assessor", job),
            Send("fit_matcher", {**job, "portfolio_items": state.get("portfolio_items", [])}),
        ]

    async def job_analyst(state: GigBidState) -> GigBidState:
        result = await nodes.analyze(state["job"])
        return {"job_spec": result.model_dump(mode="json"), "steps": 1}

    async def risk_assessor(state: GigBidState) -> GigBidState:
        result = await nodes.assess_risk(state["job"])
        return {"risk_report": result.model_dump(mode="json"), "steps": 1}

    async def fit_matcher(state: GigBidState) -> GigBidState:
        items = state.get("portfolio_items")
        result = (
            await nodes.match_fit(state["job"], portfolio_items=items)
            if items
            else await nodes.match_fit(state["job"])
        )
        return {"fit_report": result.model_dump(mode="json"), "steps": 1}

    async def decide(state: GigBidState) -> GigBidState:
        job = state["job"]
        risk = state["risk_report"]["score"]
        fit = state["fit_report"]["score"]
        remaining = state.get("connects_remaining", weekly_connects)
        ceiling = job.get("hourly_max" if job.get("payment_type") == "hourly" else "budget_max")
        if job.get("status", "open") != "open":
            decision = BidDecision(action="skip", rule_ids=["job_closed"])
        elif risk >= maximum_risk:
            decision = BidDecision(action="skip", rule_ids=["maximum_risk_score"])
        elif ceiling is not None and ceiling < state.get("price_floor", 0):
            decision = BidDecision(action="skip", rule_ids=["price_floor"])
        elif (
            fit < minimum_fit
            and watch_minimum <= fit <= watch_maximum
            and not state.get("auto_skip_low_fit", settings.autonomy.auto_skip_low_fit)
        ):
            decision = BidDecision(action="watch", rule_ids=["watch_fit_band"])
        elif fit < minimum_fit:
            decision = BidDecision(action="skip", rule_ids=["minimum_fit_score"])
        elif int(job.get("connects_cost", 0)) > remaining:
            decision = BidDecision(action="watch", rule_ids=["connects_budget"])
        else:
            decision = BidDecision(action="bid", rule_ids=["eligible"])
        return {
            "decision": decision.model_dump(mode="json"),
            "status": {"skip": "skipped", "watch": "needs_attention", "bid": "running"}[
                decision.action
            ],
            "steps": 1,
        }

    async def pricing(state: GigBidState) -> GigBidState:
        quote = await nodes.price(state)
        update: GigBidState = {"price_quote": quote.model_dump(mode="json"), "steps": 1}
        if quote.bid_type != state["job"].get("payment_type", quote.bid_type):
            update.update(_attention("The bid type does not match the job's payment type."))
            return update
        if not quote.worth_it or quote.amount <= 0 or quote.amount < state.get("price_floor", 0):
            update.update(_attention("Pricing did not meet the bidding rules."))
        return update

    async def writer(state: GigBidState) -> GigBidState:
        draft = await nodes.write(state)
        if draft.job_id != state["job_id"]:
            return _attention("The draft belongs to a different job.")
        return {
            "proposal_draft": draft.model_dump(mode="json"),
            "draft_version": state["draft_version"] + 1,
            "steps": 1,
        }

    async def verifier(state: GigBidState) -> GigBidState:
        claims = await nodes.verify(state)
        return {
            "claim_verification": [claim.model_dump(mode="json") for claim in claims],
            "steps": 1,
        }

    async def critic(state: GigBidState) -> GigBidState:
        result = await nodes.critique(state)
        update: GigBidState = {"critique": result.model_dump(mode="json"), "steps": 1}
        if result.needs_revision or not _verified(state):
            if state["revisions"] >= max_revisions:
                update.update(
                    _attention("The draft still needs revision after the allowed retries.")
                )
            else:
                update["revisions"] = state["revisions"] + 1
        return update

    def after_critic(state: GigBidState) -> str:
        if state["status"] != "running":
            return END
        if state["critique"]["needs_revision"] or not _verified(state):
            return next_if_budget(state, "writer")
        return next_if_budget(state, "compliance")

    async def compliance(state: GigBidState) -> GigBidState:
        verdict = await nodes.check_compliance(state)
        update: GigBidState = {"compliance_verdict": verdict.model_dump(mode="json"), "steps": 1}
        if not verdict.allowed or not _verified(state):
            update.update(_attention("The draft did not pass verification and compliance."))
        return update

    async def preview_submission(state: GigBidState) -> GigBidState:
        draft = ProposalDraft.model_validate(state["proposal_draft"])
        price = PriceQuote.model_validate(state["price_quote"])
        proposal = ProposalInput(
            job_id=draft.job_id,
            cover_letter=draft.cover_letter,
            screening_answers=draft.screening_answers,
            bid_amount=price.amount,
            bid_type=price.bid_type,
        )
        quote = await marketplace.preview(proposal)
        if quote.job_id != proposal.job_id:
            return _attention("The marketplace returned a quote for a different job.")
        remaining = state.get("connects_remaining", weekly_connects)
        if quote.connects_cost > remaining or quote.balance_after < 0:
            return _attention("The current Connects cost exceeds the available budget.")
        submission = proposal.model_dump(mode="json")
        preview = quote.model_dump(mode="json")
        return {
            "submission": submission,
            "preview": preview,
            "proposal_digest": _digest([state["draft_version"], submission, preview]),
            "approval_status": "pending",
            "approval_error": None,
            "status": "awaiting_approval",
            "steps": 1,
        }

    async def approval(state: GigBidState) -> GigBidState:
        response = interrupt(
            {
                "draft": state["proposal_draft"],
                "draft_version": state["draft_version"],
                "claim_verification": state["claim_verification"],
                "risk_report": state["risk_report"],
                "price_quote": state["price_quote"],
                "submission": state["submission"],
                "preview": state["preview"],
                "proposal_digest": state["proposal_digest"],
                "error": state["approval_error"],
            }
        )
        return {"approval_response": response}

    async def validate_approval(state: GigBidState) -> GigBidState:
        try:
            response = ApprovalDecision.model_validate(state["approval_response"])
        except ValidationError as error:
            explanation = error.errors(include_url=False, include_input=False)[0]["msg"]
            return {"approval_response": None, "approval_error": explanation, "steps": 1}
        if response.proposal_digest != state["proposal_digest"]:
            return {
                "approval_response": None,
                "approval_error": "Use the proposal digest in the current preview.",
                "steps": 1,
            }

        update: GigBidState = {
            "approval_response": None,
            "approval_error": None,
            "approval_decision": response.model_dump(mode="json"),
            "steps": 1,
        }
        if response.action == "approve":
            update.update(approval_status="approved", status="running")
        elif response.action == "reject":
            update.update(approval_status="rejected", status="done")
        else:
            draft = {
                **state["proposal_draft"],
                "cover_letter": response.text,
            }
            update.update(
                proposal_draft=ProposalDraft.model_validate(draft).model_dump(mode="json"),
                draft_version=state["draft_version"] + 1,
                submission=None,
                preview=None,
                proposal_digest=None,
                approval_status="edited",
                status="running",
            )
        return update

    def after_approval(state: GigBidState) -> str:
        if state["approval_error"] is not None:
            return next_if_budget(state, "approval")
        if state["approval_status"] == "approved":
            return next_if_budget(state, "submit")
        if state["approval_status"] == "edited":
            return next_if_budget(state, "verifier")
        return END

    async def submit(state: GigBidState, config: RunnableConfig) -> GigBidState:
        approval_result = ApprovalDecision.model_validate(state["approval_decision"])
        if (
            state["approval_status"] != "approved"
            or approval_result.action != "approve"
            or approval_result.proposal_digest != state["proposal_digest"]
            or state["proposal_digest"]
            != _digest([state["draft_version"], state["submission"], state["preview"]])
        ):
            return _attention("The submitted proposal must match the approved preview.")
        proposal = ProposalInput.model_validate(state["submission"])
        quote = BidPreview.model_validate(state["preview"])
        thread_id = config["configurable"]["thread_id"]
        key = "gigbid:submit:" + _digest([thread_id, state["submission"]])
        try:
            receipt = await marketplace.submit(proposal, idempotency_key=key, approved_quote=quote)
        except ConnectsQuoteChanged as error:
            return _attention(str(error))
        return {"outcome": receipt.model_dump(mode="json"), "status": "done", "steps": 1}

    async def budget_limit(state: GigBidState) -> GigBidState:
        return _attention("The bid workflow reached its configured step limit.")

    def next_if_budget(state: GigBidState, node: str) -> str:
        return node if state["steps"] < max_steps else "budget_limit"

    def next_if_running(state: GigBidState, node: str) -> str:
        return next_if_budget(state, node) if state["status"] == "running" else END

    graph = StateGraph(GigBidState, input_schema=BidGraphInput)
    for node in (
        ingest_job,
        reject_duplicate,
        job_analyst,
        risk_assessor,
        fit_matcher,
        decide,
        pricing,
        writer,
        verifier,
        critic,
        compliance,
        preview_submission,
        approval,
        validate_approval,
        submit,
        budget_limit,
    ):
        graph.add_node(node)

    graph.add_edge(START, "ingest_job")
    graph.add_conditional_edges(
        "ingest_job", fan_out, ["reject_duplicate", "job_analyst", "risk_assessor", "fit_matcher"]
    )
    graph.add_edge("reject_duplicate", END)
    graph.add_edge(["job_analyst", "risk_assessor", "fit_matcher"], "decide")
    graph.add_conditional_edges(
        "decide", lambda state: next_if_running(state, "pricing"), ["pricing", "budget_limit", END]
    )
    graph.add_conditional_edges(
        "pricing", lambda state: next_if_running(state, "writer"), ["writer", "budget_limit", END]
    )
    graph.add_conditional_edges(
        "writer",
        lambda state: next_if_running(state, "verifier"),
        ["verifier", "budget_limit", END],
    )
    graph.add_conditional_edges(
        "verifier",
        lambda state: next_if_budget(
            state, "compliance" if state.get("approval_status") == "edited" else "critic"
        ),
        ["compliance", "critic", "budget_limit"],
    )
    graph.add_conditional_edges(
        "critic", after_critic, ["writer", "compliance", "budget_limit", END]
    )
    graph.add_conditional_edges(
        "compliance",
        lambda state: next_if_running(state, "preview_submission"),
        ["preview_submission", "budget_limit", END],
    )
    graph.add_conditional_edges(
        "preview_submission",
        lambda state: (
            next_if_budget(state, "approval") if state["status"] == "awaiting_approval" else END
        ),
        ["approval", "budget_limit", END],
    )
    graph.add_edge("approval", "validate_approval")
    graph.add_conditional_edges(
        "validate_approval", after_approval, ["approval", "submit", "verifier", "budget_limit", END]
    )
    graph.add_edge("submit", END)
    graph.add_edge("budget_limit", END)
    return graph.compile(checkpointer=checkpointer, name="bid")
