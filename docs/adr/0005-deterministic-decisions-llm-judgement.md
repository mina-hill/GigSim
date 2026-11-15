# ADR 0005: Deterministic rules decide; LLMs analyse and write

**Status:** accepted · 2026-11-14

**Decision.** Bid / watch / skip, compliance blocking and memory promotion are deterministic rules over typed hand-offs, with thresholds in config. LLMs extract requirements, assess risk signals, write, critique and verify.

**Why.** Every skip and every block must be explainable in the UI and in the viva, and a policy change must be a config edit.

**Consequences.** Some judgement is lost at the decision boundary; the R&D comparison against a single ReAct agent measures what that costs.
