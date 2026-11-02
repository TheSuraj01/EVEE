# Contributing

## Setup

```powershell
py -3.12 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pre-commit install
```

Run the gate before every commit: `scripts\verify.ps1` (or `scripts/verify.sh`).

## The fastest way to contribute: add a command

Most useful contributions are one YAML entry. Add it to `config/commands.yaml` or the
relevant `plugins/*/plugin.yaml`:

```yaml
- intent: window.snap_left
  description: Snap the focused window to the left half
  risk: low
  permissions: [window.manage]
  aliases: [snap left, move this left, put this on the left]
```

Then add a handler in the matching `actions/` module and a test. **Never call an action
module directly from `nlu/` or `llm/`** — the registry and dispatcher exist so that every
command gets the same safety treatment for free.

Good aliases are phrases you have actually said out loud. Bad aliases collide with existing
ones; the registry will refuse to start if two commands claim the same phrase.

## Non-negotiables

Read `CLAUDE.md` §2. In short: nothing executes without a policy verdict; the LLM can only
return a registry intent; no dynamic execution anywhere; no secrets or raw transcripts in
logs; tests never open a microphone or a socket.

## Working with Claude Code on this repo

The repository is configured for it: `CLAUDE.md` is the contract, `docs/plan/TASKS.md` is
the backlog, `.claude/commands/` holds `/next-task`, `/run-phase`, `/verify` and
`/safety-review`. If you use it, keep its commits one-task-sized and review the safety-
relevant diffs yourself.

## Commits and PRs

Conventional Commits with the task ID: `feat(actions): window snapping [T-015]`.
Fill in the PR template's safety checklist honestly — a reviewer will check it.

## Tests

Aim for: happy path, every documented failure mode, one adversarial case. Use `FakeClock`,
`FakeTransport` and the recording handlers in `tests/conftest.py`. If you need to sleep,
you need to inject a clock instead.
