"""V0 ranks fixed-size chunks by cosine similarity to the whole job post."""

from rag.index.store import LocalIndex, SearchHit


def retrieve(query: str, index: LocalIndex, k: int = 5) -> list[SearchHit]:
    return index.dense(query, set(index.evidence), k)
