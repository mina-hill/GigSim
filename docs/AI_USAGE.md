# AI usage

The brief requires this file: which AI tools were used, for what, and one mistake they made that we caught.

## Tools

| Tool | Used for |
| --- | --- |
| Claude (Anthropic), in claude.ai with a code workspace | Planning the build, writing most of the code and tests, writing the persona bibles, drafting docs |
| Codex (OpenAI), in the Codex desktop app | Restoring the Git bundle, rebuilding the simulator-backed bid workflow, writing the integration tests and local demo, building the web workspace, and generating report tables from the smoke evaluation |

Every commit written with Claude's help says so in its message (`Co-Authored-By: Claude`).

## Files

Claude wrote first drafts of essentially every file in this repository. Every file was then reviewed, run and tested; design decisions are recorded in `docs/adr/`.

## Mistakes caught

1. **Truncated persona results.** The first persona bibles wrote results as YAML flow mappings, for example `{metric: manual invoicing, value: eliminated for 1,200 customers}`. YAML reads the comma in `1,200` as a separator, so the value silently became `eliminated for 1` and the CV said "Manual invoicing: eliminated for 1." It was caught by reading the rendered CV, and fixed by quoting every value. A test now checks the rendered corpora.
2. **Keyword echo rewarded by the simulator.** GigSim's first client scorer counted a must-have skill as covered whenever the proposal mentioned it, so a proposal that only repeated the job's keywords scored well, and off-domain proposals got replies 35% of the time. It was caught by checking reply rates by strategy in the generated past proposals. Now a skill only counts when the same sentence carries evidence ("I built ...", a number), and off-domain replies come only from label noise.
3. **Implied skills leaking into prose.** Several bible entries named a skill in the project description that was supposed to be implied only (for example Redis, Lighthouse). The test `test_case_study_prose_never_names_implied_skills` caught them; they were moved to explicit skills.
4. **Approval tied to the wrong marketplace account.** The first Codex draft bound approval to the job, proposal text and quoted cost. Review found that switching simulator tokens after reopening a checkpoint could submit for another account. The preview now records the marketplace account, and a regression test checks that a mismatched account cannot write.
