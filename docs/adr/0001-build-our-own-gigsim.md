# ADR 0001: Build our own GigSim and freeze it

**Status:** accepted · 2026-11-14

**Context.** The brief describes GigSim, persona corpora and a dev set as provided. The starter kit contained none of them; the instructor asked students to build from Kaggle data plus synthetic data.

**Decision.** Build GigSim in `gigsim/` as a separate FastAPI service with its own database, seeded from a "world" folder. GigBid never imports it; agents reach it only through the Marketplace MCP server and the marketplace port. After `gigsim-v1.0` it is frozen (`gigsim/FROZEN` + CI check); changes ship as explicit versions.

**Consequences.** Reply and hire rates measure GigSim's simulated clients, which we configured. Every result that depends on them says so. The hidden preferences never leave GigSim's database, so the agents cannot read them.
