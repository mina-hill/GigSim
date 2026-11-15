"""Deterministic single-controller and supervisor baselines for the bid study."""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import create_react_agent

from agent.config import GigBidConfig
from agent.handoffs import BidDecision
from agent.nodes.bid import OfflineBidNodes
from agent.state import GigBidState
from evals.runners.smoke import PreviewMarketplace
from mcp_servers.marketplace.port import ProposalInput

CONTROLLER_MESSAGE = "gigbid_scripted_controller"
_STAGES = ("assess", "draft", "review")


def pack(state: dict[str, Any]) -> str:
    return json.dumps({"study_state": state}, ensure_ascii=False)


def unpack(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    for message in reversed(messages):
        if not isinstance(message.content, str) or not message.content.startswith("{"):
            continue
        try:
            data = json.loads(message.content)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("study_state"), dict):
            return data["study_state"]
    raise ValueError("Missing study state in the baseline's messages.")


class ScriptedController(BaseChatModel):
    """Emit fixed tool calls through the model interface without inference or token use."""

    reference_supervisor: bool = False

    @property
    def _llm_type(self) -> str:
        return "gigbid-scripted-tools"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        return self.bind(tools=[convert_to_openai_tool(item) for item in tools], **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        state = unpack(messages)
        stage = state["stage"]
        calls = []
        if stage in _STAGES:
            calls.append(
                {
                    "name": f"transfer_to_{stage}" if self.reference_supervisor else stage,
                    "args": {} if self.reference_supervisor else {"payload": state},
                    "id": f"study-call-{len(messages)}",
                    "type": "tool_call",
                }
            )
        elif stage != "done":
            raise ValueError(f"Unknown baseline study stage {stage!r}.")
        response = AIMessage(
            content="" if calls else pack(state),
            tool_calls=calls,
            response_metadata={CONTROLLER_MESSAGE: True},
        )
        return ChatResult(generations=[ChatGeneration(message=response)])


def _decision(state: GigBidState, settings: GigBidConfig) -> BidDecision:
    """Match graph operating thresholds for the fixed open-job reference study."""
    job = state["job"]
    risk, fit = state["risk_report"]["score"], state["fit_report"]["score"]
    watch_min, watch_max = settings.thresholds["watch_fit_band"]
    minimum_fit = float(settings.thresholds["minimum_fit_score"])
    amount_field = "hourly_max" if job.get("payment_type") == "hourly" else "budget_max"
    ceiling = job.get(amount_field)
    remaining = state.get("connects_remaining", settings.connects["default_weekly_budget"])
    if state.get("duplicate") or job.get("duplicate_of"):
        return BidDecision(action="skip", rule_ids=["duplicate_posting"])
    if job.get("status", "open") != "open":
        return BidDecision(action="skip", rule_ids=["job_closed"])
    if risk >= settings.thresholds["maximum_risk_score"]:
        return BidDecision(action="skip", rule_ids=["maximum_risk_score"])
    if ceiling is not None and ceiling < state.get("price_floor", 0):
        return BidDecision(action="skip", rule_ids=["price_floor"])
    if fit < minimum_fit:
        if watch_min <= fit <= watch_max and not settings.autonomy.auto_skip_low_fit:
            return BidDecision(action="watch", rule_ids=["watch_fit_band"])
        return BidDecision(action="skip", rule_ids=["minimum_fit_score"])
    if int(job.get("connects_cost", 0)) > remaining:
        return BidDecision(action="watch", rule_ids=["connects_budget"])
    return BidDecision(action="bid", rule_ids=["eligible"])


class BaselineTools:
    def __init__(
        self,
        nodes: OfflineBidNodes,
        marketplace: PreviewMarketplace,
        settings: GigBidConfig,
    ) -> None:
        self.nodes = nodes
        self.marketplace = marketplace
        self.settings = settings

    async def assess(self, payload: dict[str, Any]) -> dict[str, Any]:
        job = payload["job"]
        state = {
            **payload,
            "job_spec": (await self.nodes.analyze(job)).model_dump(mode="json"),
            "risk_report": (await self.nodes.assess_risk(job)).model_dump(mode="json"),
            "fit_report": (await self.nodes.match_fit(job)).model_dump(mode="json"),
            "revisions": 0,
        }
        decision = _decision(state, self.settings)
        state["decision"] = decision.model_dump(mode="json")
        state["stage"] = "draft" if decision.action == "bid" else "done"
        state["status"] = {"bid": "running", "watch": "needs_attention", "skip": "skipped"}[
            decision.action
        ]
        return state

    async def draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        quote = await self.nodes.price(payload)
        state = {**payload, "price_quote": quote.model_dump(mode="json")}
        if (
            quote.bid_type != payload["job"].get("payment_type", quote.bid_type)
            or not quote.worth_it
            or quote.amount <= 0
            or quote.amount < payload.get("price_floor", 0)
        ):
            return {
                **state,
                "stage": "done",
                "status": "needs_attention",
                "errors": ["Pricing did not meet the bidding rules."],
            }
        proposal = await self.nodes.write(state)
        if proposal.job_id != payload["job"]["job_id"]:
            return {
                **state,
                "stage": "done",
                "status": "needs_attention",
                "errors": ["The draft belongs to a different job."],
            }
        return {**state, "proposal_draft": proposal.model_dump(mode="json"), "stage": "review"}

    async def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        verification = await self.nodes.verify(payload)
        state = {
            **payload,
            "claim_verification": [item.model_dump(mode="json") for item in verification],
        }
        critique = await self.nodes.critique(state)
        state["critique"] = critique.model_dump(mode="json")
        all_supported = all(item.supported for item in verification)
        if critique.needs_revision or not all_supported:
            ceiling = min(int(self.settings.budgets["max_revisions"]), 2)
            if state["revisions"] >= ceiling:
                return {
                    **state,
                    "stage": "done",
                    "status": "needs_attention",
                    "errors": ["The draft still needs revision after the allowed retries."],
                }
            return {**state, "stage": "draft", "revisions": state["revisions"] + 1}

        verdict = await self.nodes.check_compliance(state)
        state["compliance_verdict"] = verdict.model_dump(mode="json")
        if not verdict.allowed:
            return {
                **state,
                "stage": "done",
                "status": "needs_attention",
                "errors": ["The draft did not pass verification and compliance."],
            }
        draft, quote = state["proposal_draft"], state["price_quote"]
        proposal = ProposalInput(
            job_id=draft["job_id"],
            cover_letter=draft["cover_letter"],
            screening_answers=draft["screening_answers"],
            bid_amount=quote["amount"],
            bid_type=quote["bid_type"],
        )
        preview = await self.marketplace.preview(proposal)
        if preview.job_id != proposal.job_id or preview.balance_after < 0:
            return {
                **state,
                "stage": "done",
                "status": "needs_attention",
                "errors": ["The marketplace preview did not match the request."],
            }
        return {
            **state,
            "stage": "done",
            "status": "awaiting_approval",
            "submission": proposal.model_dump(mode="json"),
            "preview": preview.model_dump(mode="json"),
        }


def single_react(tools: BaselineTools):
    def wrapped(name: str, handler: Callable[[dict], Awaitable[dict]]):
        async def run(payload: dict[str, Any]) -> dict[str, Any]:
            return {"study_state": await handler(payload)}

        return tool(name, description=f"Execute the {name} stage against the public job.")(run)

    available = [wrapped(name, getattr(tools, name)) for name in _STAGES]
    return create_react_agent(ScriptedController(), available, checkpointer=InMemorySaver())


def _worker(name: str, handler: Callable[[dict], Awaitable[dict]]):
    async def run(state: MessagesState) -> MessagesState:
        update = await handler(unpack(state["messages"]))
        return {"messages": [AIMessage(name=name, content=pack(update))]}

    graph = StateGraph(MessagesState)
    graph.add_node("perform", run)
    graph.add_edge(START, "perform")
    graph.add_edge("perform", END)
    return graph.compile(name=name)


def supervisor_reference(tools: BaselineTools):
    try:
        from langgraph_supervisor import create_supervisor
    except ImportError as error:
        raise RuntimeError("Install langgraph-supervisor==0.0.31 for the reference arm.") from error

    workers = [_worker(name, getattr(tools, name)) for name in _STAGES]
    return create_supervisor(
        workers,
        model=ScriptedController(reference_supervisor=True),
        output_mode="last_message",
        add_handoff_back_messages=False,
    ).compile(checkpointer=InMemorySaver())
