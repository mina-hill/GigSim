"""A small SQLite response cache for LangChain chat models.

Why: evaluation runs repeat the same prompts many times across variants, and free API tiers
have tight rate limits. Caching by (model settings, prompt) makes reruns free and results
reproducible. Changing a prompt changes its hash, so edited prompts always reach the model.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.caches import RETURN_VAL_TYPE, BaseCache
from langchain_core.load import dumps, loads
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, Generation

# Only these classes may be rebuilt from the cache file (no arbitrary object loading).
_ALLOWED = [Generation, ChatGeneration, ChatGenerationChunk, AIMessage, AIMessageChunk]


class SQLiteLLMCache(BaseCache):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS llm_cache ("
                " key TEXT PRIMARY KEY, llm_string TEXT, generations TEXT,"
                " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _key(prompt: str, llm_string: str) -> str:
        return hashlib.sha256(f"{llm_string}\x00{prompt}".encode()).hexdigest()

    def lookup(self, prompt: str, llm_string: str) -> RETURN_VAL_TYPE | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT generations FROM llm_cache WHERE key = ?", (self._key(prompt, llm_string),)
            ).fetchone()
        if row is None:
            return None
        return loads(row[0], allowed_objects=_ALLOWED)

    def update(self, prompt: str, llm_string: str, return_val: RETURN_VAL_TYPE) -> None:
        payload = dumps(list(return_val))
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, llm_string, generations) VALUES (?, ?, ?)",
                (self._key(prompt, llm_string), llm_string, payload),
            )

    def clear(self, **kwargs: Any) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM llm_cache")

    def size(self) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]


def keys_for(prompts: Sequence[str], llm_string: str) -> list[str]:
    """Exposed for tests and for tooling that inspects the cache."""
    return [SQLiteLLMCache._key(p, llm_string) for p in prompts]
