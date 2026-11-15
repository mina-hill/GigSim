"""Read-only MCP access to a single portfolio corpus and its evidence index."""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agent.config import get_config
from agent.prompts.loader import render_prompt, wrap_untrusted
from mcp_servers.runtime import ROOT, make_server, run_server, server_parser, tool_hints
from rag.corpus import Evidence, load_corpus
from rag.retriever import Retriever

EvidenceType = Literal["cv", "case_study", "repo", "review", "past_proposal"]
FilterValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
EvidenceId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")]
Requirement = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]


class EvidenceFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: FilterValue | None = None
    type: EvidenceType | None = None
    project_id: FilterValue | None = None
    date: FilterValue | None = None
    client_industry: FilterValue | None = None
    outcome: FilterValue | None = None
    skill: FilterValue | None = None


class EvidenceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    persona: str
    type: EvidenceType
    source: str
    title: str
    text: str
    project_id: str | None
    skills: tuple[str, ...]
    date: str | None
    client_industry: str | None
    outcome: str | None


class EvidenceHit(BaseModel):
    evidence: EvidenceItem
    score: float


class EvidenceResults(BaseModel):
    matches: list[EvidenceHit]


class Skills(BaseModel):
    skills: list[str]


def create_portfolio_server(corpus: list[Evidence], retriever: Retriever | None = None) -> FastMCP:
    server = make_server(
        "GigBid Portfolio",
        "mcp-portfolio",
        "Search the configured portfolio and cite its source IDs when drafting.",
    )
    personas = {item.persona for item in corpus}
    if len(personas) != 1 or len({item.id for item in corpus}) != len(corpus):
        raise ValueError("Configure one nonempty portfolio corpus with unique evidence IDs.")
    items = {item.id: item for item in corpus}
    index = retriever if retriever is not None else Retriever(corpus)
    read = tool_hints()

    def lookup(evidence_id: str, expected: EvidenceType) -> EvidenceItem:
        item = items.get(evidence_id)
        if not item or item.type != expected:
            raise ToolError("Evidence item not found in the configured portfolio.")
        return EvidenceItem.model_validate(item)

    @server.tool(annotations=read)
    async def search_evidence(
        requirement: Requirement,
        k: Annotated[int, Field(ge=1, le=20)] = 5,
        filters: EvidenceFilters | None = None,
    ) -> EvidenceResults:
        """Retrieve scoped evidence for a requirement, with source IDs and relevance scores."""
        options = filters.model_dump(exclude_none=True) if filters else None
        hits = await anyio.to_thread.run_sync(
            partial(index.search, requirement, k=k, filters=options)
        )
        return EvidenceResults(
            matches=[
                EvidenceHit(evidence=lookup(hit.evidence.id, hit.evidence.type), score=hit.score)
                for hit in hits
            ]
        )

    @server.tool(annotations=read)
    def get_case_study(id: EvidenceId) -> EvidenceItem:
        """Read a portfolio case study by evidence ID or project ID."""
        return lookup(id if id.startswith("cs-") else f"cs-{id}", "case_study")

    @server.tool(annotations=read)
    def list_skills() -> Skills:
        """List skills recorded in the portfolio, excluding requirements from past job posts."""
        return Skills(
            skills=sorted(
                {skill for item in corpus if item.type != "past_proposal" for skill in item.skills},
                key=str.casefold,
            )
        )

    @server.resource("portfolio://case/{id}", mime_type="text/markdown")
    def case_study(id: str) -> str:
        """Read the case study text for a stable evidence ID."""
        return get_case_study(id).text

    @server.resource("portfolio://proposal/{id}", mime_type="text/markdown")
    def past_proposal(id: str) -> str:
        """Read a past proposal from the configured portfolio."""
        return lookup(id, "past_proposal").text

    @server.resource("portfolio://repo/{name}", mime_type="text/markdown")
    def repository(name: str) -> str:
        """Read available repository content by name."""
        return lookup(f"repo-{name}", "repo").text

    @server.prompt(title="Answer a screening question from my portfolio")
    def screening_answer(question: Requirement) -> str:
        """Draft an evidence-backed answer to a screening question."""
        return render_prompt("portfolio_screening", question=wrap_untrusted(question, "question"))

    @server.prompt(title="Which of my projects prove this requirement?")
    def prove_requirement(requirement: Requirement) -> str:
        """Find portfolio projects that support a stated requirement."""
        return render_prompt(
            "portfolio_requirement",
            requirement=wrap_untrusted(requirement, "requirement"),
        )

    @server.prompt(title="Draft an opening line for this job")
    def draft_opening(job: Requirement) -> str:
        """Write a grounded opening to a job proposal."""
        return render_prompt("portfolio_opening", job=wrap_untrusted(job))

    return server


def main() -> None:
    parser = server_parser("Run the GigBid Portfolio MCP server.", 8101)
    parser.add_argument(
        "--persona",
        default=os.environ.get("MCP_PORTFOLIO_PERSONA", "fullstack"),
        choices=("ai_ml", "fullstack", "data_analyst"),
    )
    parser.add_argument("--corpus", type=Path, help="Load one persona corpus from this directory.")
    args = parser.parse_args()
    corpus = load_corpus(args.corpus or ROOT / "data" / "personas" / args.persona)
    index = Retriever.from_config(corpus, get_config().retrieval)
    run_server(create_portfolio_server(corpus, index), args, "MCP_PORTFOLIO_TOKEN")


if __name__ == "__main__":
    main()
