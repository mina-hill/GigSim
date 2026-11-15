"""V3 jointly scores a query and candidate chunks, then reorders the shortlist."""

from rag.backends import Reranker
from rag.index.store import LocalIndex, SearchHit


def rerank(
    query: str,
    candidates: list[SearchHit],
    k: int,
    index: LocalIndex,
    reranker: Reranker,
) -> list[SearchHit]:
    if not candidates:
        return []
    documents = [index.representative_chunk(hit.evidence.id, query) for hit in candidates]
    scores = reranker.score(query, documents)
    ranked = [SearchHit(hit.evidence, score) for hit, score in zip(candidates, scores, strict=True)]
    return sorted(ranked, key=lambda hit: -hit.score)[:k]
