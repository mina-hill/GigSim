"""Chunk, index and rank portfolio documents with stable document-level IDs."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from ..backends import Embedder, VectorMatrix
from ..corpus import Evidence
from .lexical import BM25, tokens


@dataclass(frozen=True, slots=True)
class SearchHit:
    evidence: Evidence
    score: float


@dataclass(frozen=True, slots=True)
class Chunk:
    evidence_id: str
    text: str


def chunk_text(evidence: Evidence) -> Iterable[Chunk]:
    words = evidence.text.split()
    for start in range(0, max(1, len(words)), 112):
        body = " ".join(words[start : start + 128])
        yield Chunk(evidence.id, f"{evidence.title}\n{body}")
        if start + 128 >= len(words):
            break


class LocalIndex:
    def __init__(self, evidence: Sequence[Evidence], embedder: Embedder) -> None:
        self.embedder = embedder
        self.chunks: list[Chunk] = []
        self.vectors: VectorMatrix = np.empty((0, 0), dtype=np.float32)
        self.refresh(evidence)

    def refresh(self, evidence: Sequence[Evidence]) -> None:
        """Reuse embeddings of unchanged chunks when a document is added, edited or removed."""
        records = {item.id: item for item in evidence}
        if len(records) != len(evidence):
            raise ValueError("Evidence ids must be unique")
        reusable = dict(zip(self.chunks, self.vectors, strict=True))
        chunks = [chunk for item in evidence for chunk in chunk_text(item)]
        missing = list(dict.fromkeys(chunk for chunk in chunks if chunk not in reusable))
        if missing:
            vectors = self.embedder.encode([chunk.text for chunk in missing])
            reusable.update(zip(missing, vectors, strict=True))
        matrix = np.stack([reusable[chunk] for chunk in chunks]) if chunks else np.empty((0, 0))
        bm25 = BM25([tokens(chunk.text) for chunk in chunks])
        documents: dict[str, list[str]] = {item.id: [] for item in evidence}
        for chunk in chunks:
            documents[chunk.evidence_id].append(chunk.text)
        self.evidence = records
        self.chunks = chunks
        self.vectors = np.asarray(matrix, dtype=np.float32)
        self.bm25 = bm25
        self.documents = documents

    def _best_documents(
        self, scores: Iterable[tuple[int, float]], allowed: set[str], k: int
    ) -> list[SearchHit]:
        best: dict[str, float] = {}
        for index, score in scores:
            evidence_id = self.chunks[index].evidence_id
            if evidence_id in allowed and score > best.get(evidence_id, 0):
                best[evidence_id] = score
        ranked = sorted(best, key=lambda evidence_id: (-best[evidence_id], evidence_id))
        return [
            SearchHit(self.evidence[evidence_id], best[evidence_id]) for evidence_id in ranked[:k]
        ]

    def dense(self, query: str, allowed: set[str], k: int) -> list[SearchHit]:
        if not self.chunks or not allowed or not tokens(query):
            return []
        vector = self.embedder.encode([query])[0]
        scores = np.einsum("ij,j->i", self.vectors, vector)
        return self._best_documents(enumerate(map(float, scores)), allowed, k)

    def sparse(self, query: str, allowed: set[str], k: int) -> list[SearchHit]:
        return self._best_documents(self.bm25.score(tokens(query)).items(), allowed, k)

    def representative_chunk(self, evidence_id: str, query: str) -> str:
        query_terms = set(tokens(query))
        return max(
            self.documents[evidence_id],
            key=lambda text: len(query_terms.intersection(tokens(text))),
        )
