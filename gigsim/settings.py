"""GigSim settings, read from the environment (prefix GIGSIM_)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class GigSimSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GIGSIM_", env_file=".env", extra="ignore")

    db_url: str = "sqlite:///./.cache/gigsim.db"
    world_path: str = "data/processed/gigsim_world"
    seed: int = 4016
    # token:account_id pairs, comma separated
    api_tokens: str = (
        "dev-token-ai:persona_ai_ml,dev-token-web:persona_fullstack,"
        "dev-token-data:persona_data_analyst"
    )
    admin_token: str = "dev-admin"  # noqa: S105  (dev default; set GIGSIM_ADMIN_TOKEN)

    # Faults and limits (changeable at runtime through /admin/faults)
    rate_limit_per_minute: int = 60
    error_rate: float = 0.03
    latency_ms_max: int = 150

    # Simulated time: one tick is this many simulated hours
    tick_hours: int = 1
    base_time: str = "2026-11-01T00:00:00+00:00"
    policy_version: str = "v1"

    # Simulated clients: "rules" (deterministic, free) or "llm" (rules + an LLM judgement)
    client_mode: str = "rules"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "llama-3.3-70b-versatile"
    llm_api_key_env: str = "GROQ_API_KEY"

    def token_map(self) -> dict[str, str]:
        pairs = [p.strip() for p in self.api_tokens.split(",") if p.strip()]
        out: dict[str, str] = {}
        for pair in pairs:
            token, _, account = pair.partition(":")
            if token and account:
                out[token] = account
        return out


@lru_cache(maxsize=1)
def get_settings() -> GigSimSettings:
    return GigSimSettings()
