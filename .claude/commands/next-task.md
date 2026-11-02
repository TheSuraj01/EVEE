---
description: Pick and fully implement the next task from the backlog, test-first.
---

Execute one unit of work end to end. Do not ask me for confirmation at any step unless
CLAUDE.md §5 applies.

1. Read `docs/plan/TASKS.md`. Select the lowest-numbered unchecked task whose
   dependencies are all ticked. If it is marked 🔴, stop and tell me exactly what you
   need from me.
2. State in two lines: the task ID, and your implementation plan.
3. Write the failing test(s) first.
4. Implement the minimum code that satisfies the acceptance criteria and respects every
   invariant in CLAUDE.md §2.
5. Run: `python -m pytest -q && ruff check . && ruff format --check . && mypy src`.
   Iterate until green. Never tick a box on a red gate.
6. Tick the checkbox in `docs/plan/TASKS.md`, append one line to `docs/plan/PROGRESS.md`.
7. `git add -A && git commit -m "<type>(<scope>): <summary> [<TASK-ID>]"`.
8. Report in ≤5 lines: what changed, what the tests prove, what is next.
