# X2 · Paired proposal openings

We tested whether an evidence-first opening would increase GigSim's simulated reply rate
compared with asking a focused question about the first milestone.

From 300 eligible good-fit synthetic development jobs, 215 yielded comparable pairs. Each
simulated client contributed at most one job. Both drafts in a pair shared the case-study
evidence, body, screening answers, quote, and simulator randomness. The proposal code saw
public job fields and persona evidence. Frozen GigSim v1.0 decision rules alone saw the
simulated client's hidden preferences. An interview or reply counted when the client did
not go quiet. No proposals were submitted and no Connects were spent.

| Paired outcome | Jobs |
| --- | ---: |
| Both openings received a reply | 125 |
| Evidence-first only | 3 |
| Question-first only | 4 |
| Neither received a reply | 83 |

Evidence-first received 128 of 215 replies (59.53%). Question-first received 129 (60.00%).
The paired difference was −0.465 percentage points, with a two-sided exact McNemar
`p = 1.0`. The experiment found no evidence of a reply-rate difference in frozen GigSim.
[Pair-level results and exclusions](../evals/results/x2.pairs.json)

The original pilot had no replies in either arm across 223 pairs. We added the evidence
source's explicit skills and outcome to the shared body after inspecting the pilot. The
follow-up is exploratory and used the same development data. Its seven discordant pairs
give little power to detect a small effect. The rule-based simulator cannot establish how
real clients respond.

Reproduce the pilot and follow-up with `uv run --extra rag python -m evals.runners.x2`.
