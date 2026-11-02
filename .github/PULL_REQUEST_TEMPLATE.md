## What and why

<!-- One paragraph. Link the task ID from docs/plan/TASKS.md. -->

Task: T-___

## Safety checklist

- [ ] No new path reaches `actions/` without passing `PolicyEngine.evaluate()`
- [ ] No `eval` / `exec` / `os.system` / `shell=True` on transcript- or model-derived values
- [ ] New commands were added to `config/commands.yaml` (or a plugin manifest), not hardcoded
- [ ] Any new risk ≥ HIGH command requires confirmation
- [ ] No secret, raw transcript or audio reaches a log, the audit file or a commit
- [ ] Tests touch no microphone and no network

## Gate

- [ ] `python -m pytest -q`
- [ ] `ruff check . && ruff format --check .`
- [ ] `mypy src`
- [ ] Checkbox ticked in `docs/plan/TASKS.md`, line appended to `docs/plan/PROGRESS.md`
