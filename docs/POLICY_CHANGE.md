# Local policy change drill

The course's platform policy notice was unavailable. This drill tests the change path with
a synthetic rule that blocks a reference to an external scheduling portal. The rule lives in
a temporary test copy of the policy pack and applies to both proposals and messages. Before
the rule is added, both guards allow the fixture. After reloading that copy, both guards block
the same text and return the configured reason. Neither guard needs source changes.

```bash
uv run pytest -q tests/unit/test_policy_change.py
```

With the course's update, edit `data/policy_pack/rules.yaml` and mirror the rule in
`marketplace_policy.md`. Add the official allow/block examples to the tests, restart the
runtime so it reloads the rule pack, and rerun the approval flows. Record the notice, edits,
timing, and results alongside the existing test evidence.
