---
id: cs-ai-p05
project_id: ai-p05
type: case_study
persona: ai_ml
title: OCR invoice parser
client_industry: logistics
date: 2023-08
skills:
- OCR
- document AI
- FastAPI
synthetic: true
---
# OCR invoice parser

For a logistics client (2023-05 to 2023-08). Accounts payable typed 40,000 invoices a month by hand.

Combined OCR with a layout-aware model to extract vendor, totals and line items. Wrapped it in an API with a review queue for low-confidence fields.

Outcome: field-level accuracy: 94%; invoices processed automatically: 40,000 per month.
