---
id: cs-da-p03
project_id: da-p03
type: case_study
persona: data_analyst
title: dbt project
client_industry: ecommerce
date: 2024-07
skills:
- dbt
- data warehouse
- data tests
synthetic: true
---
# dbt project

**What was the problem?**

Every dashboard calculated revenue slightly differently.

**How did you solve it?**

staging, intermediate and mart models with documented definitions; tests for uniqueness, nulls and accepted values on key tables.

**Did it work?**

Yes: conflicting revenue numbers in leadership meetings: none since launch; models with tests: 38 of 38.
