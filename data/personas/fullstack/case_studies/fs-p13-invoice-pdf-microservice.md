---
id: cs-fs-p13
project_id: fs-p13
type: case_study
persona: fullstack
title: invoice PDF microservice
client_industry: fintech
date: 2022-06
skills:
- Node.js
- PDF generation
synthetic: true
---
# invoice PDF microservice

**Context.** Invoice PDFs were generated synchronously and timed out under load.

**What I did**

- Queue-backed service rendering branded PDFs from HTML templates

**Results**

- invoice generation failures: from 3% to 0.1%

Tools: Node.js, PDF generation
