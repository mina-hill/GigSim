---
id: cs-ai-p01
project_id: ai-p01
type: case_study
persona: ai_ml
title: HelioDocs RAG
client_industry: healthcare
date: 2025-05
skills:
- RAG
- LangChain
- pgvector
- FastAPI
- evaluation
synthetic: true
---
# HelioDocs RAG

For a healthcare client (2025-02 to 2025-05). Support staff spent hours searching 3,400 pages of internal policy PDFs and often quoted outdated versions.

Chunked PDFs by section with page numbers and indexed them in pgvector with hybrid keyword search. Answers cite the page and refuse when no passage supports them. Built a 200-question evaluation set with the support lead and ran it on every change.

Outcome: citation accuracy: 91% on 200 labelled questions; correct refusals on out-of-scope questions: 96%.
