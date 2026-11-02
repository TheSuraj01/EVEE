---
description: Run the full quality gate and fix everything that fails.
---

Run, in this order, and fix every failure before reporting:

```
python -m pytest -q
ruff check .
ruff format --check .
mypy src
```

Then audit against CLAUDE.md §2 and report any violation you find with a file:line
reference:
- any `eval`, `exec`, `os.system`, `shell=True`, or `subprocess` reachable from a
  transcript or an LLM response
- any action executed without passing through `PolicyEngine.evaluate()`
- any LLM call on a path that a local alias could have resolved
- any network or microphone access inside `tests/`
- any secret, API key, or raw transcript written to a log or committed file
- any import that violates the dependency direction in CLAUDE.md §6

Output a table: check · status · evidence. Fix what you can; list what you cannot.
