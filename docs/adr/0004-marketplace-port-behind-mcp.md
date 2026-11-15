# ADR 0004: The marketplace port sits behind our Marketplace MCP server

**Status:** accepted · 2026-11-14

**Decision.** Graph → Marketplace MCP server tools → `MarketplacePort` → GigSim adapter (REST) or Upwork adapter (Upwork's MCP server). `marketplace.mode` in config picks the adapter.

**Why.** R1 and A4 require that the graph cannot tell which marketplace it uses. Putting the port behind our own server keeps one set of tool names, schemas, annotations, idempotency and audit logging for both modes.

**Consequences.** Switching to live mode changes config and credentials only. Upwork-specific differences live in one adapter file.
