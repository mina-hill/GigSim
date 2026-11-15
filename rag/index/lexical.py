"""Stable tokenization and an inverted BM25 index."""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

_TOKEN = re.compile(r"[a-z0-9]+(?:[.#_+-][a-z0-9]+)*(?:\+\+|#)?", re.IGNORECASE)
_STOP = frozenset(
    "a an and are as at be by can for from has have how i in is it my of on or our that the "
    "their this to use was we were what when which will with would you your".split()
)


def tokens(text: str) -> list[str]:
    return [term for term in _TOKEN.findall(text.casefold()) if term not in _STOP]


class BM25:
    def __init__(self, documents: Sequence[Sequence[str]]) -> None:
        postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        lengths = [len(document) for document in documents]
        average_length = sum(lengths) / max(1, len(lengths)) or 1
        for index, document in enumerate(documents):
            normalizer = 1.2 * (0.25 + 0.75 * lengths[index] / average_length)
            for term, count in Counter(document).items():
                postings[term].append((index, count * 2.2 / (count + normalizer)))
        self.postings = {
            term: [(idx, weight * self._idf(len(documents), len(hits))) for idx, weight in hits]
            for term, hits in postings.items()
        }

    @staticmethod
    def _idf(total: int, frequency: int) -> float:
        return math.log1p((total - frequency + 0.5) / (frequency + 0.5))

    def score(self, query: Iterable[str]) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for term in dict.fromkeys(query):
            for index, weight in self.postings.get(term, ()):
                scores[index] += weight
        return scores
