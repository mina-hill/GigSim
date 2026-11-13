"""Load agent/config.yaml with ${VAR:-default} environment interpolation.

Everything a later phase tunes (model routing, thresholds, budgets, prompt versions, policy
paths) is read from this file, so the code never hard-codes a model name or a rule.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "agent" / "config.yaml"

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _interpolate(value: Any) -> Any:
    """Replace ${VAR} and ${VAR:-default} inside strings, recursively."""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


class ModelRef(BaseModel):
    provider: str
    model: str


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str | None = None


class ProfileConfig(BaseModel):
    tiers: dict[str, ModelRef]
    fallbacks: dict[str, ModelRef] = {}


class ModelDefaults(BaseModel):
    temperature: float = 0.0
    timeout_s: float = 60
    max_retries: int = 2


class ModelsConfig(BaseModel):
    profile: str
    defaults: ModelDefaults = ModelDefaults()
    providers: dict[str, ProviderConfig]
    profiles: dict[str, ProfileConfig]
    nodes: dict[str, str]
    prompt_versions: dict[str, str] = {}

    @field_validator("nodes")
    @classmethod
    def _nodes_not_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("models.nodes must map every node to a tier")
        return v


class AutonomyConfig(BaseModel):
    auto_skip_low_fit: bool = False
    auto_send: bool = False

    @field_validator("auto_send")
    @classmethod
    def _never_auto_send(cls, v: bool) -> bool:
        # R4: nothing leaves the system without a human approval. Not configurable.
        if v:
            raise ValueError(
                "autonomy.auto_send must be false; GigBid never sends without approval"
            )
        return v


class GigBidConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int
    marketplace: dict[str, Any]
    models: ModelsConfig
    retrieval: dict[str, Any]
    budgets: dict[str, Any]
    thresholds: dict[str, Any]
    autonomy: AutonomyConfig
    connects: dict[str, Any] = {}
    policy_pack: dict[str, Any] = {}

    # --- convenience lookups -------------------------------------------------
    def active_profile(self) -> ProfileConfig:
        name = self.models.profile
        if name not in self.models.profiles:
            raise KeyError(f"Unknown model profile {name!r}; known: {sorted(self.models.profiles)}")
        return self.models.profiles[name]

    def model_for_node(self, node: str) -> ModelRef:
        tier = self.models.nodes.get(node)
        if tier is None:
            raise KeyError(f"Node {node!r} has no tier in models.nodes")
        return self.active_profile().tiers[tier]

    def fallback_for_node(self, node: str) -> ModelRef | None:
        tier = self.models.nodes[node]
        return self.active_profile().fallbacks.get(tier)


def load_config(path: str | Path | None = None, env_file: str | Path | None = None) -> GigBidConfig:
    """Read and validate the config. Environment variables override ${...} placeholders."""
    load_dotenv(env_file or REPO_ROOT / ".env", override=False)
    raw = yaml.safe_load(Path(path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    return GigBidConfig.model_validate(_interpolate(raw))


@lru_cache(maxsize=1)
def get_config() -> GigBidConfig:
    """Process-wide cached config. Call get_config.cache_clear() in tests after changing env."""
    return load_config()
