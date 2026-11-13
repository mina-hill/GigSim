---
id: cs-ai-p12
project_id: ai-p12
type: case_study
persona: ai_ml
title: inbox agent
client_industry: real estate
date: 2026-01
skills:
- LangGraph
- AI agents
- tool calling
- human approval
synthetic: true
---
# inbox agent

**What was the problem?**

Agents spent their mornings answering repetitive buyer emails.

**How did you solve it?**

langGraph agent that classifies emails, drafts replies from listing data and books viewings; every draft waits for an agent's approval before it is sent.

**Did it work?**

Yes: drafts accepted without edits: 60%; average reply time: from 6 hours to 45 minutes.

An excerpt from `agent/graph.py`:

```
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class EmailState(TypedDict, total=False):
    email: str
    intent: str
    draft: str
```
