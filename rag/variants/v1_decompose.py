"""V1 searches each requirement, then merges document ranks."""

from collections.abc import Callable, Iterable

from rag.index.store import SearchHit

from .v2_hybrid import reciprocal_rank_fusion


def retrieve(
    requirements: Iterable[str],
    dense: Callable[[str, int], list[SearchHit]],
    k: int,
) -> list[SearchHit]:
    return reciprocal_rank_fusion(
        [dense(requirement, max(k, 20)) for requirement in requirements], k
    )
