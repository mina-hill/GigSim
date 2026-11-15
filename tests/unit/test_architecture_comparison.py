import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.config import get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.nodes.bid import OfflineBidNodes
from evals.runners.architecture_baselines import (
    BaselineTools,
    ScriptedController,
    pack,
    single_react,
    supervisor_reference,
    unpack,
)
from evals.runners.architectures import repost_signals
from evals.runners.smoke import PreviewMarketplace


def test_scripted_model_has_distinct_controller_and_supervisor_calls():
    state = {"stage": "draft", "job": {"job_id": "fixture"}}
    initial = [HumanMessage(content=pack(state))]
    single = ScriptedController().invoke(initial)
    reference = ScriptedController(reference_supervisor=True).invoke(initial)
    assert single.tool_calls[0]["name"] == "draft"
    assert single.tool_calls[0]["args"] == {"payload": state}
    assert reference.tool_calls[0]["name"] == "transfer_to_draft"
    assert reference.tool_calls[0]["args"] == {}
    done = {"stage": "done", "status": "skipped"}
    completed = ScriptedController().invoke([HumanMessage(content=pack(done))])
    assert unpack([completed]) == done


def test_repost_signal_requires_same_client_and_near_match():
    text = (
        "Please build a Python data loading pipeline with separate extraction and validation "
        "from our billing exports into the reporting database"
    )
    originals = {
        "first": {"title": "Build a Python pipeline", "description": text, "client_id": "alice"},
        "repost": {
            "title": "Build a Python pipeline (reposted)",
            "description": text,
            "client_id": "alice",
        },
        "independent": {
            "title": "Build a Python pipeline (reposted)",
            "description": text,
            "client_id": "bob",
        },
        "unrelated": {
            "title": "Repost a lettering project",
            "description": "Looking for a hand-drawn illustration",
            "client_id": "alice",
        },
    }
    assert repost_signals(originals) == {"repost"}


async def test_actual_supervisor_and_react_use_same_validated_fixture_as_bid_graph():
    pytest.importorskip("langgraph_supervisor")
    root = Path(__file__).resolve().parents[2]
    source = root / "gigsim" / "fixtures" / "mini_world" / "jobs.jsonl"
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    job = next(row for row in records if row["job_id"] == "job_web_stripe")
    job = {key: value for key, value in job.items() if key != "hidden"}
    job["connects_cost"] = job["base_connects"]
    settings = get_config()
    nodes = OfflineBidNodes.from_persona("fullstack", config=settings)
    marketplace = PreviewMarketplace({job["job_id"]: job}, "fullstack", 80)
    tools = BaselineTools(nodes, marketplace, settings)
    run_config = {"configurable": {"thread_id": "test:stripe-study"}}

    graph = build_bid_graph(
        nodes=nodes,
        marketplace=marketplace,
        checkpointer=InMemorySaver(),
        config=settings,
    )
    expected = await graph.ainvoke({"job": job}, config=run_config)
    assert expected["__interrupt__"]
    inputs = {"messages": [HumanMessage(content=pack({"job": job, "stage": "assess"}))]}
    for workflow in (single_react(tools), supervisor_reference(tools)):
        result = await workflow.ainvoke(inputs, config=run_config)
        preview = unpack(result["messages"])
        assert preview["decision"] == expected["decision"]
        assert preview["status"] == "awaiting_approval"
        assert preview["submission"] == expected["submission"]
        assert preview["claim_verification"] == expected["claim_verification"]
