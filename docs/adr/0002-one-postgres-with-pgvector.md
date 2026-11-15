# ADR 0002: One Postgres with pgvector for data, checkpoints, memory and vectors

**Status:** accepted · 2026-11-14

**Decision.** Postgres 16 with the pgvector extension holds application data, LangGraph checkpoints (`AsyncPostgresSaver`), long-term memory (`AsyncPostgresStore`), embeddings and the audit log. BM25 runs in-process with `bm25s`.

**Why.** One database to deploy, back up and explain; tenant scoping is a SQL `WHERE`. Qdrant would add native hybrid search but a second stateful service on a small VM.

**Consequences.** Hybrid fusion (RRF) is our own ~20 lines of code, which is also easy to defend in the viva.
