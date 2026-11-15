"""Run or resume a local, simulator-only bid with a human reviewer."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.config import get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.marketplace_gateway import LocalBidMarketplace
from agent.nodes.bid import OfflineBidNodes
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter


def review(preview):
    print(json.dumps(preview, indent=2, ensure_ascii=False))
    digest = preview["proposal_digest"]
    while True:
        action = input("[a] Approve, [e] Edit, [r] Reject, [q] Save and exit: ").strip().lower()
        if action == "a":
            return {"action": "approve", "proposal_digest": digest}
        if action == "e":
            return {
                "action": "edit",
                "proposal_digest": digest,
                "text": input("Enter revised cover letter: "),
            }
        if action == "r":
            return {
                "action": "reject",
                "proposal_digest": digest,
                "reason": input("Enter rejection reason: "),
            }
        if action == "q":
            return None


async def run(args):
    settings = get_config()
    if settings.marketplace["mode"] != "gigsim":
        raise SystemExit("The local bid demo requires MARKETPLACE_MODE=gigsim.")
    token = os.environ.get("GIGBID_SIM_TOKEN")
    if not token:
        raise SystemExit("Set GIGBID_SIM_TOKEN for the selected GigSim persona account.")

    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    base_url = httpx.URL(settings.marketplace["gigsim"]["base_url"])
    realm = hashlib.sha256(str(base_url).encode()).hexdigest()
    async with (
        httpx.AsyncClient(base_url=base_url, timeout=10) as client,
        AsyncSqliteSaver.from_conn_string(str(args.checkpoint)) as saver,
    ):
        adapter = GigSimAdapter(client, token=token)
        account = await adapter.get_connects_balance()
        if account.account_id != f"persona_{args.persona}":
            raise SystemExit("GIGBID_SIM_TOKEN must belong to the selected persona's account.")
        thread = {"configurable": {"thread_id": f"{realm}:{account.account_id}:{args.job_id}"}}
        graph = build_bid_graph(
            nodes=OfflineBidNodes.from_persona(args.persona, config=settings),
            marketplace=LocalBidMarketplace(adapter),
            checkpointer=saver,
            config=settings,
        )
        state = await graph.aget_state(thread)
        if not state.values:
            result = await graph.ainvoke({"job_id": args.job_id}, thread, durability="sync")
        elif state.interrupts:
            result = {**state.values, "__interrupt__": state.interrupts}
        elif state.next:
            if not args.retry:
                raise SystemExit("The run stopped mid-step. Use --retry to resume it.")
            result = await graph.ainvoke(None, thread, durability="sync")
        else:
            result = state.values

        while result.get("__interrupt__"):
            decision = review(result["__interrupt__"][0].value)
            if decision is None:
                print("Approval saved for review. Rerun the same command to continue.")
                return
            result = await graph.ainvoke(Command(resume=decision), thread, durability="sync")

        for field in ("status", "decision", "approval_decision", "errors", "outcome"):
            if field in result:
                print(json.dumps({field: result[field]}, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--persona", choices=["ai_ml", "fullstack", "data_analyst"], required=True)
    parser.add_argument("--checkpoint", type=Path, default=Path(".cache/bid_demo.sqlite"))
    parser.add_argument("--retry", action="store_true", help="Resume a run that stopped mid-step.")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
