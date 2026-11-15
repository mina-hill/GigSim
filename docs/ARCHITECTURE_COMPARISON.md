# Part C · Orchestration comparison

We tested whether the hand-built graph's explicit control flow changes bid outcomes or
local runtime when every design uses the same deterministic analysis, pricing, writer,
verifier, critic, and policy tools. The comparison uses LangGraph's prebuilt ReAct loop,
the published LangGraph Supervisor package, and GigBid's production StateGraph.
[LangGraph Supervisor 0.0.31](https://pypi.org/project/langgraph-supervisor/0.0.31/)

All three arms processed the same 50 stratified synthetic development jobs with
checkpointing. ReAct and Supervisor used the same scripted tool controller. The measured
scope ends when a proposal is ready for human review. The frozen GigSim client scorer
simulated replies separately, with the same randomness for a job in each arm.

| Design | Correct initial decisions | F1 | At review | Flagged claims | Policy blocks | Simulated replies | Local p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Single deterministic ReAct controller | 38/50 | 0.7778 | 27 | 0/51 | 0 | 0/27 | 15.770 ms |
| LangGraph Supervisor with deterministic controller | 38/50 | 0.7778 | 27 | 0/51 | 0 | 0/27 | 25.731 ms |
| Hand-built bid graph | 38/50 | 0.7778 | 27 | 0/51 | 0 | 0/27 | 9.193 ms |

The shared tools gave the same decisions and draft scores. The hand-built graph reached
review faster on this workstation. There were no model tokens, and the timings exclude
startup and client scoring. All arms used synthetic proposals and simulated outcomes.
The zero blocked cases leave the relative ability to catch policy violations unmeasured
on this sample. [Raw outcomes and timings](../evals/results/architecture.dev.json)

## Development failures and replay

| Failure in the recorded comparison | Jobs | Replay outcome |
| --- | ---: | --- |
| Below-floor job marked bid initially | 6 | Feeding the existing persona floor into the initial decision fixed all six. The downstream pricing check had already withheld these drafts. |
| Duplicate reached review with no duplicate marker | 6 | Public wording explicitly calling the post a repost, plus high word overlap with another listing from the same client, caught three. |
| Correct initial bid with no simulated reply | 21 | The replay targeted classification. The client scorer's reasons, including missing evidenced must-have skills, are retained for proposal improvements. |

The diagnostic replay raised initial decision accuracy from 38/50 to 47/50. It used public
listing fields and persona rates, with no duplicate labels passed to the graph. These
changes were applied to the replay only. The raw artifact records each affected job ID and
the decision before and after. [Failure annotations and replay](../evals/results/architecture.dev.json)

Reproduce with `uv run --extra eval python -m evals.runners.architectures`.
