# Local checkpoint trace samples

These files come from the persona-scoped run trace export in the GigBid API. The
sample run used two newly authored synthetic job postings, a seeded full-stack demo
persona, and fresh SQLite databases for the simulator and workflow checkpoints.

| Sample | What the run showed |
| --- | --- |
| [Skipped scam](skipped-scam.json) | The workflow assessed a posting that asked applicants for a deposit and an unpaid test. It stopped at the bid decision. |
| [Pending before reopening the API](pending-before-restart.json) | The billing job reached its approval checkpoint with an empty simulator proposal pipeline. |
| [Pending after reopening the API](pending-after-restart.json) | A fresh FastAPI app instance reopened the same workflow database. The trace ID, timeline, and pending approval digest matched the original run. |
| [Approved proposal](approved-proposal.json) | The restored run accepted approval and reached the submit node. The simulator contained exactly one proposal afterward. |

The final three files show stages of the same run. The export includes a synthetic job
ID, persona, run status, checkpoint times, and node statuses. The approval digest and
proposal outcome were checked inside the test process. Use **Download run trace** in
the job's **Run timeline** to capture a fresh example from your local app.
