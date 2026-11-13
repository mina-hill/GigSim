---
id: cs-da-p09
project_id: da-p09
type: case_study
persona: data_analyst
title: Google Sheets tracker
client_industry: nonprofit
date: 2022-11
skills:
- Google Sheets
- automation
synthetic: true
---
# Google Sheets tracker

**Context.** Volunteers tracked donations on paper and lost stock counts.

**What I did**

- Shared Sheets tracker with form entry and low-stock email alerts

**Results**

- stock-count errors: down 80%

Tools: Google Sheets, automation

An excerpt from `Code.gs`:

```
function checkLowStock() {
  const sheet = SpreadsheetApp.getActive().getSheetByName("Inventory");
  const rows = sheet.getDataRange().getValues().slice(1);
  const low = rows.filter(r => r[2] < r[3]).map(r => `${r[0]}: ${r[2]} left`);
  if (low.length) MailApp.sendEmail("coordinator@example.org", "Low stock", low.join("\n"));
}
```
