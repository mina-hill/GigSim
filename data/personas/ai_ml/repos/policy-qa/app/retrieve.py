"""Hybrid retrieval over policy chunks with page citations."""
from dataclasses import dataclass

import psycopg
from pgvector.psycopg import register_vector
from rank_bm25 import BM25Okapi


@dataclass
class Passage:
    doc_id: str
    page: int
    text: str
    score: float


def dense_search(conn: psycopg.Connection, query_vec, k: int = 20) -> list[Passage]:
    register_vector(conn)
    rows = conn.execute(
        "SELECT doc_id, page, text, 1 - (embedding <=> %s) FROM chunks ORDER BY embedding <=> %s LIMIT %s",
        (query_vec, query_vec, k),
    ).fetchall()
    return [Passage(*r) for r in rows]


def rrf(*rankings: list[Passage], k: int = 60) -> list[Passage]:
    scores: dict[tuple[str, int], float] = {}
    by_key: dict[tuple[str, int], Passage] = {}
    for ranking in rankings:
        for rank, p in enumerate(ranking):
            key = (p.doc_id, p.page)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            by_key[key] = p
    return [by_key[key] for key in sorted(scores, key=scores.get, reverse=True)]
