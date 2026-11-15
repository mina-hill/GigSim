# ADR 0003: Route every node to a model through config, over OpenAI-compatible APIs

**Status:** accepted · 2026-11-14

**Decision.** `agent/config.yaml` maps each node to a tier (small, strong, judge) and each profile maps tiers to provider + model. Groq, OpenRouter, Ollama and vLLM are all reached through the OpenAI-compatible chat API with one client class. Each tier may have a fallback on a second provider. All calls go through a SQLite response cache.

**Why.** The brief requires per-node routing in config, an open profile, and comparing at least two open configurations. One client class keeps that a config change. The cache makes evaluation reruns free and reproducible on free-tier rate limits.

**Consequences.** Provider-specific features (Anthropic prompt caching, native tool formats) are not used. Model ids must be checked against each provider before an evaluation run.
