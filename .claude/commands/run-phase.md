---
description: Work continuously through every remaining task in a phase. Usage - /run-phase 2
argument-hint: "<phase number>"
---

Complete **all** remaining tasks in Phase $ARGUMENTS of `docs/plan/TASKS.md`, in order,
without pausing between them.

For each task, follow the full `/next-task` loop: test first, implement, gate green,
tick the box, log progress, commit.

Rules for this run:
- Do not ask for permission between tasks. Keep going.
- If a task is blocked (🔴, or a genuine CLAUDE.md §5 stop), skip it, record the blocker
  in `docs/plan/PROGRESS.md` as `BLOCKED: <id> — <reason>`, and continue with the next
  unblocked task.
- If you break a previously passing test, fix it before moving on.
- Keep each commit scoped to a single task.

At the end, print a phase report: tasks completed, tasks blocked and why, test count,
coverage of the new modules, and the first task of the next phase.
