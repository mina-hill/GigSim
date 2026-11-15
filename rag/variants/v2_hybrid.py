"""V2 fuses dense and BM25 document ranks."""

from collections import defaultdict
from collections.abc import Iterable, Sequence

from rag.corpus import Evidence
from rag.index.store import SearchHit


def reciprocal_rank_fusion(rankings: Iterable[Sequence[SearchHit]], k: int) -> list[SearchHit]:
    documents: dict[str, Evidence] = {}
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            eid = hit.evidence.id
            documents[eid] = hit.evidence
            scores[eid] += 1 / (60 + rank)
    ranked = sorted(scores, key=lambda eid: (-scores[eid], eid))
    return [SearchHit(documents[eid], scores[eid]) for eid in ranked[:k]]
