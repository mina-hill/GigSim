"""V8 grades evidence against requirements and performs one corrective retrieval."""

from collections.abc import Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from rag.corpus import Evidence
from rag.index.lexical import tokens
from rag.index.store import SearchHit

from .v2_hybrid import reciprocal_rank_fusion

Ablation = Literal["without_bm25", "without_reranker", "without_retry"]


class _State(TypedDict, total=False):
    query: str
    requirements: list[str]
    k: int
    allowed: set[str]
    matches: list[SearchHit]
    gaps: list[str]
    reworked: bool
    ablation: Ablation | None


def _covers(evidence: Evidence, requirement: str) -> bool:
    if evidence.type == "past_proposal":
        return False
    terms = set(tokens(requirement))
    evidence_terms = set(tokens(f"{evidence.title} {evidence.text} {' '.join(evidence.skills)}"))
    return bool(terms) and terms <= evidence_terms


def corrective_graph(
    hybrid: Callable[[str, set[str], int, bool], list[SearchHit]],
    rerank: Callable[[str, list[SearchHit], int], list[SearchHit]],
):
    def retrieve(state: _State) -> _State:
        query, k, allowed = state["query"], state["k"], state["allowed"]
        sparse = state.get("ablation") != "without_bm25"
        if state.get("reworked"):
            candidates = reciprocal_rank_fusion(
                [
                    state["matches"],
                    *(hybrid(gap, allowed, max(k, 15), sparse) for gap in state["gaps"]),
                ],
                max(k, 20),
            )
        else:
            candidates = hybrid(query, allowed, max(k, 20), sparse)
        matches = (
            candidates[: max(k, 15)]
            if state.get("ablation") == "without_reranker"
            else rerank(query, candidates, max(k, 15))
        )
        return {"matches": matches}

    def grade(state: _State) -> _State:
        matches = [
            hit
            for hit in state["matches"]
            if any(_covers(hit.evidence, requirement) for requirement in state["requirements"])
        ]
        gaps = [
            requirement
            for requirement in state["requirements"]
            if not any(_covers(hit.evidence, requirement) for hit in matches[: state["k"]])
        ]
        return {"matches": matches, "gaps": gaps}

    def decide(state: _State) -> str:
        can_retry = state.get("ablation") != "without_retry" and not state.get("reworked")
        return "retry" if state["gaps"] and can_retry else END

    def retry(state: _State) -> _State:
        return {"reworked": True}

    graph: StateGraph[_State, None, _State, _State] = StateGraph(_State)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade", grade)
    graph.add_node("retry", retry)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges("grade", decide, ["retry", END])
    graph.add_edge("retry", "retrieve")
    return graph.compile()
