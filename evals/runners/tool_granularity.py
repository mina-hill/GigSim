"""Compare Marketplace MCP tool shapes with fixed routing on read-only fixture cases."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal

import httpx
from mcp.client.session import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel, Field

from gigsim.app import app
from gigsim.db import get_engine
from gigsim.settings import get_settings
from mcp_servers.audit import AuditLog
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
from mcp_servers.marketplace.port import (
    ClientHistory,
    ConnectsBalance,
    ConnectsQuote,
    Event,
    Job,
    JobId,
    JobPage,
    MarketplacePort,
    PaymentType,
    Proposal,
    Thread,
)
from mcp_servers.marketplace.server import create_marketplace_server
from mcp_servers.runtime import ROOT, make_server, tool_hints

_WORLD = ROOT / "gigsim" / "fixtures" / "mini_world"
_CASES = ROOT / "evals" / "datasets" / "tool_granularity.jsonl"
_COMMAND = (
    "uv run python -m evals.runners.tool_granularity "
    "--output evals/results/tool_granularity.report.json "
    "--details evals/results/tool_granularity.cases.json"
)
_SETTINGS = {
    "GIGSIM_DB_URL": "sqlite:///:memory:",
    "GIGSIM_ERROR_RATE": "0",
    "GIGSIM_LATENCY_MS_MAX": "0",
    "GIGSIM_RATE_LIMIT_PER_MINUTE": "1000",
    "GIGSIM_CLIENT_MODE": "rules",
    "GIGSIM_POLICY_VERSION": "v1",
    "GIGSIM_API_TOKENS": "granularity-fixture:persona_fullstack",
    "GIGSIM_ADMIN_TOKEN": "granularity-fixture-admin",
    "GIGSIM_BASE_TIME": "2026-11-01T00:00:00+00:00",
}
_ACCOUNT_TOOLS = {
    "balance": "get_connects_balance",
    "proposals": "list_proposals",
    "threads": "list_threads",
    "events": "list_events",
}


class Inspection(BaseModel):
    job: Job | None = None
    history: ClientHistory | None = None
    quote: ConnectsQuote | None = None


class AccountSnapshot(BaseModel):
    balance: ConnectsBalance | None = None
    proposals: list[Proposal] | None = None
    threads: list[Thread] | None = None
    events: list[Event] | None = None


def bundled_server(port: MarketplacePort) -> FastMCP:
    server = make_server("GigBid grouped benchmark", "mcp-marketplace", "Read synthetic job data.")
    read = tool_hints(external=True)

    @server.tool(annotations=read)
    async def inspect_job(
        job_id: JobId,
        sections: Annotated[list[Literal["job", "history", "quote"]], Field(min_length=1)],
    ) -> Inspection:
        """Fetch job details, client history or current Connects quote in one call."""
        job = await port.get_job(job_id) if {"job", "history"}.intersection(sections) else None
        return Inspection(
            job=job if "job" in sections else None,
            history=await port.get_client_history(job.client.client_id)
            if job and "history" in sections
            else None,
            quote=await port.quote_connects(job_id) if "quote" in sections else None,
        )

    @server.tool(annotations=read)
    async def search_marketplace(
        q: str | None = None,
        domain: str | None = None,
        payment_type: PaymentType | None = None,
    ) -> JobPage:
        """Search the open marketplace by text, domain or payment type."""
        return await port.search_jobs(q=q, domain=domain, payment_type=payment_type)

    @server.tool(annotations=read)
    async def account_snapshot(
        sections: Annotated[
            list[Literal["balance", "proposals", "threads", "events"]], Field(min_length=1)
        ],
    ) -> AccountSnapshot:
        """Fetch selected account views in one call."""
        return AccountSnapshot(
            balance=await port.get_connects_balance() if "balance" in sections else None,
            proposals=await port.list_proposals() if "proposals" in sections else None,
            threads=await port.list_threads() if "threads" in sections else None,
            events=await port.list_events() if "events" in sections else None,
        )

    return server


class Case(BaseModel):
    id: str
    instruction: str
    mode: Literal["job", "search", "account"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    sections: list[str] = Field(default_factory=list)
    expected: dict[str, Any]


def _size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


class Meter:
    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self.calls: list[dict[str, Any]] = []
        self.request_bytes = 0
        self.response_bytes = 0

    async def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        request = {"name": name, "arguments": arguments}
        response = await self.session.call_tool(name, arguments=arguments)
        if response.isError or response.structuredContent is None:
            raise RuntimeError(f"Benchmark tool {name} failed: {response.content}")
        self.calls.append(request)
        self.request_bytes += _size(request)
        self.response_bytes += _size(response.structuredContent)
        return response.structuredContent


async def _run(case: Case, meter: Meter, *, bundled: bool) -> dict[str, Any]:
    if case.mode == "search":
        return await meter.call(
            "search_marketplace" if bundled else "search_jobs", **case.arguments
        )
    if bundled:
        name = "inspect_job" if case.mode == "job" else "account_snapshot"
        result = await meter.call(name, sections=case.sections, **case.arguments)
        return {key: result[key] for key in case.sections}
    if case.mode == "account":
        result = {}
        for section in case.sections:
            response = await meter.call(_ACCOUNT_TOOLS[section])
            result[section] = response if section == "balance" else response["result"]
        return result
    job = None
    result = {}
    if {"job", "history"}.intersection(case.sections):
        job = await meter.call("get_job", **case.arguments)
        if "job" in case.sections:
            result["job"] = job
    if "history" in case.sections and job is not None:
        result["history"] = await meter.call(
            "get_client_history", client_id=job["client"]["client_id"]
        )
    if "quote" in case.sections:
        result["quote"] = await meter.call("quote_connects", **case.arguments)
    return result


def _expected(result: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        if key == "matches_job":
            if value not in {job["job_id"] for job in result["items"]}:
                return False
            continue
        node: Any = result
        for segment in key.split("."):
            node = node[segment]
        if node != value:
            return False
    return True


@asynccontextmanager
async def _fixture_port() -> AsyncIterator[MarketplacePort]:
    previous = {key: os.environ.get(key) for key in _SETTINGS}
    os.environ.update(_SETTINGS)
    get_settings.cache_clear()
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://gigsim.test"
            ) as client,
        ):
            seed = await client.post(
                "/admin/seed",
                json={"world_path": str(_WORLD)},
                headers={
                    "X-Admin-Token": _SETTINGS["GIGSIM_ADMIN_TOKEN"],
                },
            )
            seed.raise_for_status()
            token = _SETTINGS["GIGSIM_API_TOKENS"].partition(":")[0]
            yield GigSimAdapter(client, token=token)
    finally:
        get_engine().dispose()
        get_settings.cache_clear()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def compare(cases: list[Case]) -> dict[str, Any]:
    with TemporaryDirectory() as temp:
        async with _fixture_port() as port:
            granular = create_marketplace_server(port, AuditLog(Path(temp) / "audit.db"))
            coarse = bundled_server(port)
            return await _compare_servers(cases, granular, coarse)


async def _compare_servers(cases: list[Case], granular: FastMCP, coarse: FastMCP) -> dict[str, Any]:
    profiles = ("granular", "bundled")
    totals = {}
    results = []
    async with (
        create_connected_server_and_client_session(granular) as first,
        create_connected_server_and_client_session(coarse) as second,
    ):
        sessions = (first, second)
        for profile, session in zip(profiles, sessions, strict=True):
            definitions = [
                tool.model_dump(exclude_none=True) for tool in (await session.list_tools()).tools
            ]
            totals[profile] = {
                "tools": len(definitions),
                "schema_json_bytes": _size(definitions),
                "calls": 0,
                "request_json_bytes": 0,
                "response_json_bytes": 0,
                "verified_cases": 0,
            }
        for case in cases:
            meters = [Meter(session) for session in sessions]
            before = await _run(case, meters[0], bundled=False)
            after = await _run(case, meters[1], bundled=True)
            paired = before == after and _expected(before, case.expected)
            result = {"id": case.id, "paired_success": paired, "tool_calls": {}}
            for profile, meter in zip(profiles, meters, strict=True):
                totals[profile]["calls"] += len(meter.calls)
                totals[profile]["request_json_bytes"] += meter.request_bytes
                totals[profile]["response_json_bytes"] += meter.response_bytes
                totals[profile]["verified_cases"] += int(paired)
                result["tool_calls"][profile] = meter.calls
            results.append(result)
    return {
        "benchmark": "tool_granularity",
        "fixture": "gigsim/fixtures/mini_world at initial tick, fullstack account",
        "routing": "Fixed oracle routes. No LLM tool selection or token counts measured.",
        "measurement": "Canonical UTF-8 JSON payload bytes. "
        "Excludes JSON-RPC envelopes and text duplicates.",
        "case_count": len(cases),
        "profiles": totals,
        "cases": results,
    }


def _summary(details: dict[str, Any]) -> dict[str, Any]:
    metrics = []
    for profile, counts in details["profiles"].items():
        for description, key in (
            ("Tools advertised", "tools"),
            ("Tool calls", "calls"),
            ("Request JSON bytes", "request_json_bytes"),
            ("Response JSON bytes", "response_json_bytes"),
            ("Advertised schema JSON bytes", "schema_json_bytes"),
        ):
            metrics.append({"name": f"{description} ({profile})", "value": counts[key]})
        metrics.append(
            {
                "name": f"Verified cases ({profile})",
                "value": counts["verified_cases"],
                "denominator": details["case_count"],
            }
        )
    return {
        "schema_version": 1,
        "experiment": "Marketplace MCP tool granularity",
        "scope": (
            "Both toolsets used fixed routing for 40 read-only cases against the same seeded "
            "GigSim fixture. Payload sizes are canonical UTF-8 JSON bytes. Tool selection by "
            "a language model and provider token usage are outside this measurement."
        ),
        "command": _COMMAND,
        "metrics": metrics,
    }


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=_CASES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--details", type=Path)
    args = parser.parse_args()
    cases = [
        Case.model_validate_json(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cases) != 40 or len({case.id for case in cases}) != len(cases):
        raise SystemExit("The comparison needs 40 independently identified cases.")
    details = asyncio.run(compare(cases))
    report = _summary(details)
    if args.output:
        _write(args.output, report)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    if args.details:
        _write(args.details, details)
    if not all(result["paired_success"] for result in details["cases"]):
        raise SystemExit("Some comparison cases failed to match fixture expectations.")


if __name__ == "__main__":
    main()
