"""Dense embeddings and query-document ranking backends."""

from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from hashlib import blake2b
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from .index.lexical import tokens

VectorMatrix = NDArray[np.float32]


class Embedder(Protocol):
    name: str

    def encode(self, texts: Sequence[str]) -> VectorMatrix: ...


class Reranker(Protocol):
    name: str

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


@lru_cache(maxsize=32_768)
def _bucket(term: str, width: int) -> int:
    value = blake2b(term.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(value, "little") % width


class HashedEmbedder:
    name = "hash-bow-1024"

    def encode(self, texts: Sequence[str]) -> VectorMatrix:
        width = 1024
        matrix = np.zeros((len(texts), width), dtype=np.float32)
        for row, text in enumerate(texts):
            for term, count in Counter(tokens(text)).items():
                matrix[row, _bucket(term, width)] += 1 + np.log(count)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        np.divide(matrix, norms, out=matrix, where=norms > 0)
        return matrix


class LexicalReranker:
    """Local reranking substitute for a model cross-encoder."""

    name = "lexical-pair"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        terms = tokens(query)
        unique = set(terms)
        pairs = set(zip(terms, terms[1:], strict=False))
        results = []
        for document in documents:
            doc_terms = tokens(document)
            overlap = len(unique.intersection(doc_terms)) / max(1, len(unique))
            doc_pairs = set(zip(doc_terms, doc_terms[1:], strict=False))
            phrase_overlap = len(pairs & doc_pairs) / max(1, len(pairs))
            results.append(0.8 * overlap + 0.2 * phrase_overlap)
        return results


class SentenceEmbeddings:
    def __init__(self, model: str, *, device: str = "cpu") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "Install sentence-transformers to use a local embedding model."
            ) from error

        self.name = model
        self.model = SentenceTransformer(model, device=device, local_files_only=True)

    def encode(self, texts: Sequence[str]) -> VectorMatrix:
        vectors = self.model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)


class CrossEncoderReranker:
    def __init__(self, model: str, *, device: str = "cpu") -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as error:
            raise RuntimeError(
                "Install sentence-transformers to use a local cross-encoder."
            ) from error

        self.name = model
        self.model = CrossEncoder(model, device=device, local_files_only=True)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        scores = self.model.predict(
            [(query, document) for document in documents], show_progress_bar=False
        )
        return [float(score) for score in scores]
