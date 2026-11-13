---
id: cs-ai-p06
project_id: ai-p06
type: case_study
persona: ai_ml
title: ticket classifier
client_industry: saas
date: 2023-02
skills:
- text classification
- DistilBERT
- queues
synthetic: true
---
# ticket classifier

**Context.** Tickets waited in a shared inbox until someone routed them by hand.

**What I did**

- Fine-tuned DistilBERT on 25,000 historical tickets across 14 queues
- Background workers classify new tickets within seconds and send unsure ones to a human

**Results**

- tickets routed automatically: 72%
- median time to first response: from 5 hours to 40 minutes

Tools: text classification, DistilBERT, queues
