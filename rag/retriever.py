"""Select, configure and refresh retrieval variants for a scoped portfolio."""

import re
from collections.abc import Sequence
from threading import Lock
from typing import Any, Literal

from .backends import (
    CrossEncoderReranker,
    Embedder,
    HashedEmbedder,
    LexicalReranker,
    Reranker,
    SentenceEmbeddings,
)
from .corpus import Evidence
from .index.store import LocalIndex, SearchHit
from .variants.v1_decompose import retrieve as decompose
from .variants.v2_hybrid import reciprocal_rank_fusion
from .variants.v3_rerank import rerank
from .variants.v8_corrective import Ablation, corrective_graph

Variant = Literal["v0", "v1", "v2", "v3", "v8"]
VARIANTS: tuple[Variant, ...] = ("v0", "v1", "v2", "v3", "v8")
FILTERS = frozenset(
    {"persona", "type", "project_id", "date", "client_industry", "outcome", "skill"}
)
_COMMENTS = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_INVISIBLE = str.maketrans("", "", "\u200b\u200c\u200d\u2060\ufeff")


def job_query(job: dict[str, Any]) -> str:
    raw = "\n".join(str(job.get(field) or "") for field in ("title", "description"))
    return _COMMENTS.sub(" ", raw.translate(_INVISIBLE)).strip()


def _eligible(evidence: Evidence, filters: dict[str, str]) -> bool:
    for key, value in filters.items():
        if key == "skill":
            if value.casefold() not in {skill.casefold() for skill in evidence.skills}:
                return False
        elif str(getattr(evidence, key, None)).casefold() != value.casefold():
            return False
    return True


class Retriever:
    def __init__(
        self,
        evidence: Sequence[Evidence],
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        default_variant: Variant = "v2",
        corrective_rerank: bool = True,
    ) -> None:
        if default_variant not in VARIANTS:
            raise ValueError(f"Unknown retrieval variant: {default_variant}")
        self.embedder = embedder if embedder is not None else HashedEmbedder()
        self.reranker = reranker if reranker is not None else LexicalReranker()
        self.default_variant = default_variant
        self.corrective_rerank = corrective_rerank
        self.index = LocalIndex(evidence, self.embedder)
        self.corrective = corrective_graph(self._hybrid, self._rerank)
        self._lock = Lock()

    @classmethod
    def from_config(cls, evidence: Sequence[Evidence], config: dict[str, Any]) -> "Retriever":
        embeddings = config.get("embeddings", {})
        reranker = config.get("reranker", {})
        for options in (embeddings, reranker):
            if options.get("backend", "offline") not in {"offline", "sentence-transformers"}:
                raise ValueError("Unknown retrieval backend")
        return cls(
            evidence,
            embedder=SentenceEmbeddings(embeddings["model"], device=embeddings.get("device", "cpu"))
            if embeddings.get("backend") == "sentence-transformers"
            else None,
            reranker=CrossEncoderReranker(reranker["model"], device=reranker.get("device", "cpu"))
            if reranker.get("backend") == "sentence-transformers"
            else None,
            default_variant=config.get("variant", "v2"),
            corrective_rerank=config.get("corrective_rerank", True),
        )

    def refresh(self, evidence: Sequence[Evidence]) -> None:
        with self._lock:
            self.index.refresh(evidence)

    def retrieve_job(
        self,
        job: dict[str, Any],
        *,
        k: int = 5,
        variant: Variant | None = None,
        filters: dict[str, str] | None = None,
        ablation: Ablation | None = None,
    ) -> list[SearchHit]:
        return self.search(
            job_query(job),
            k=k,
            variant=variant,
            filters=filters,
            ablation=ablation,
            requirements=job.get("skills") or job.get("required_skills") or (),
        )

    def _hybrid(
        self, query: str, allowed: set[str], k: int, sparse: bool = True
    ) -> list[SearchHit]:
        if not sparse:
            return self.index.dense(query, allowed, k)
        depth = max(k, 30)
        return reciprocal_rank_fusion(
            [self.index.dense(query, allowed, depth), self.index.sparse(query, allowed, depth)], k
        )

    def _rerank(self, query: str, candidates: list[SearchHit], k: int) -> list[SearchHit]:
        return rerank(query, candidates, k, self.index, self.reranker)

    def search(
        self,
        requirement: str,
        *,
        k: int = 5,
        variant: Variant | None = None,
        filters: dict[str, str] | None = None,
        requirements: Sequence[str] = (),
        ablation: Ablation | None = None,
    ) -> list[SearchHit]:
        active = variant or self.default_variant
        if active not in VARIANTS:
            raise ValueError(f"Unknown retrieval variant: {active}")
        if ablation is not None and active != "v8":
            raise ValueError("Corrective ablations only apply to v8")
        if k < 1:
            raise ValueError("k must be positive")
        if not requirement.strip():
            return []
        filters = filters or {}
        if unknown := filters.keys() - FILTERS:
            raise ValueError(f"Unknown evidence filters: {', '.join(sorted(unknown))}")
        with self._lock:
            allowed = {eid for eid, item in self.index.evidence.items() if _eligible(item, filters)}
            if not allowed:
                return []

            if active == "v0":
                return self.index.dense(requirement, allowed, k)
            if active == "v2":
                return self._hybrid(requirement, allowed, k)
            if active == "v3":
                return self._rerank(requirement, self._hybrid(requirement, allowed, max(k, 20)), k)
            parts = list(
                dict.fromkeys(
                    text.strip()
                    for text in (requirements or re.split(r"[,;\n]+", requirement))
                    if text.strip()
                )
            )
            if active == "v1":
                return decompose(
                    parts, lambda query, depth: self.index.dense(query, allowed, depth), k
                )
            stage = ablation or (None if self.corrective_rerank else "without_reranker")
            result = self.corrective.invoke(
                {
                    "query": requirement,
                    "requirements": parts,
                    "k": k,
                    "allowed": allowed,
                    "ablation": stage,
                }
            )
            return result["matches"][:k]
