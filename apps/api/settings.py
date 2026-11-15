"""Server-only configuration for the synthetic marketplace accounts."""

import os

# Public synthetic fixture credentials from .env.example.
_DEV_TOKEN_PAIRS = (
    "dev-token-ai:persona_ai_ml,dev-token-web:persona_fullstack,"  # noqa: S105
    "dev-token-data:persona_data_analyst"
)


def load_simulator_account_tokens() -> dict[str, str]:
    """Return the configured GigSim bearer token for each simulator account."""
    pairs = os.environ.get("GIGSIM_API_TOKENS", _DEV_TOKEN_PAIRS)
    accounts_by_token: dict[str, str] = {}
    for pair in pairs.split(","):
        token, _, account_id = pair.strip().partition(":")
        if token and account_id:
            accounts_by_token[token] = account_id
    return {account_id: token for token, account_id in accounts_by_token.items()}
