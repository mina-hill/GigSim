"""One place that turns a graph node's name into a chat model.

Which model a node uses is decided by agent/config.yaml (profile -> tier -> provider/model),
never by code (brief: "Route by node ... set in config, never in code"). Every provider is
reached through the OpenAI-compatible chat API, so Groq, OpenRouter, Ollama and vLLM share one
client class. Responses go through an optional SQLite cache, and each node may have a fallback
model on a second provider for when the first is rate-limited or down.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict, deque
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from langchain_core.caches import BaseCache
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable

from agent.config import GigBidConfig, ModelRef, get_config
from agent.llm_cache import SQLiteLLMCache

# --------------------------------------------------------------------------- fake model


_FAKE_QUEUES: dict[str, deque[str]] = defaultdict(deque)


def queue_fake_responses(model: str, responses: Iterable[str]) -> None:
    """Tests: make the fake model named `model` answer with these strings, in order."""
    _FAKE_QUEUES[model].extend(responses)


def clear_fake_responses() -> None:
    _FAKE_QUEUES.clear()


class DeterministicFakeChat(BaseChatModel):
    """Offline stand-in used by the `test` profile (unit tests, CI, Playwright).

    Returns queued responses if a test queued any, otherwise a stable string derived from
    the prompt, so the same input always gives the same output.
    """

    model: str = "fake"

    @property
    def _llm_type(self) -> str:
        return "gigbid-fake"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model}

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: Any = None, **kwargs: Any) -> ChatResult:
        queue = _FAKE_QUEUES.get(self.model)
        if queue:
            text = queue.popleft()
        else:
            joined = "\n".join(str(m.content) for m in messages)
            digest = hashlib.sha256(joined.encode()).hexdigest()
            text = f"[{self.model}:{digest[:12]}]"
        message = AIMessage(
            content=text,
            usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


# --------------------------------------------------------------------------- cache


@lru_cache(maxsize=1)
def _shared_cache() -> BaseCache | None:
    if os.environ.get("LLM_CACHE_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return None
    return SQLiteLLMCache(os.environ.get("LLM_CACHE_PATH", ".cache/llm_cache.sqlite"))


# --------------------------------------------------------------------------- factory


def build_chat_model(ref: ModelRef, cfg: GigBidConfig, *, use_cache: bool = True,
                     temperature: float | None = None) -> BaseChatModel:
    """Instantiate the chat model a ModelRef points at."""
    defaults = cfg.models.defaults
    if ref.provider == "fake":
        return DeterministicFakeChat(model=ref.model)

    from langchain_openai import ChatOpenAI  # imported lazily: tests that use `fake` skip it

    provider = cfg.models.providers.get(ref.provider)
    if provider is None or not provider.base_url:
        raise KeyError(f"Provider {ref.provider!r} is not configured with a base_url")
    api_key = os.environ.get(provider.api_key_env, "") if provider.api_key_env else "not-needed"
    if provider.api_key_env and not api_key:
        raise RuntimeError(
            f"{provider.api_key_env} is not set; add it to .env or switch MODEL_PROFILE"
        )
    return ChatOpenAI(
        model=ref.model,
        base_url=provider.base_url,
        api_key=api_key,
        temperature=defaults.temperature if temperature is None else temperature,
        timeout=defaults.timeout_s,
        max_retries=defaults.max_retries,
        cache=_shared_cache() if use_cache else False,
    )


def get_chat_model(node: str, *, use_cache: bool = True, cfg: GigBidConfig | None = None,
                   temperature: float | None = None) -> BaseChatModel:
    """The primary chat model for a node (no fallback). Use for streaming or tool binding."""
    cfg = cfg or get_config()
    return build_chat_model(cfg.model_for_node(node), cfg, use_cache=use_cache,
                            temperature=temperature)


def get_runnable(node: str, *, schema: type | None = None, use_cache: bool = True,
                 cfg: GigBidConfig | None = None) -> Runnable:
    """The node's model with its fallback attached, optionally with structured output.

    `schema` is a Pydantic model; the result then returns instances of it.
    """
    cfg = cfg or get_config()
    primary: Runnable = get_chat_model(node, use_cache=use_cache, cfg=cfg)
    if schema is not None:
        primary = primary.with_structured_output(schema)  # type: ignore[attr-defined]
    fallback_ref = cfg.fallback_for_node(node)
    if fallback_ref is None:
        return primary
    try:
        fallback: Runnable = build_chat_model(fallback_ref, cfg, use_cache=use_cache)
    except RuntimeError:
        return primary  # fallback provider has no key configured; run without it
    if schema is not None:
        fallback = fallback.with_structured_output(schema)  # type: ignore[attr-defined]
    return primary.with_fallbacks([fallback])


def describe_routing(cfg: GigBidConfig | None = None) -> dict[str, str]:
    """node -> 'provider:model' for the active profile (printed by `make models`)."""
    cfg = cfg or get_config()
    out = {}
    for node in cfg.models.nodes:
        ref = cfg.model_for_node(node)
        out[node] = f"{ref.provider}:{ref.model}"
    return out
