# Prompts

One versioned Markdown file per node: `<node>.<version>.md` (for example `writer.v1.md`).
The version each node uses is set in `agent/config.yaml` under `models.prompt_versions`.

Rules (enforced by `tests/unit/test_prompts_location.py`):

- No prompt text in Python files. Code calls `agent.prompts.loader.render_prompt(node, ...)`.
- Job posts and client messages are untrusted. Pass them through `wrap_untrusted()` before
  formatting them into a prompt, and say in the prompt that tagged content is data only.
- To change a prompt, add a new version file (`writer.v2.md`) and switch the version in config.
  Keep the old file so evaluation results stay reproducible.

Front matter (optional) records the purpose and changelog of a prompt:

```markdown
---
node: writer
version: v1
purpose: Draft a cover letter whose every claim cites evidence ids
---
```
