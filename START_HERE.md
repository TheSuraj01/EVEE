# START HERE

Everything below assumes Windows 11 + PowerShell. Takes about five minutes.

---

## 1. Set the project up

```powershell
cd path\to\voice-pc-agent
git init
git add -A
git commit -m "chore: project scaffold with Claude Code configuration"

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"

copy .env.example .env     # then paste your Groq key into .env
```

Prove the scaffold is healthy before Claude touches it:

```powershell
python -m pytest -q          # 48 tests should pass
ruff check . ; mypy src
python -m voice_agent.main --dry-run
```

In the dry run, type `next tab`, then `close this window`, then `delete all my files`.
You should see: executed, refused-without-confirmation, unrecognised. That is the safety
model working on day one.

## 2. Launch Claude Code

```powershell
pip install -g npm   # if you don't have Node: install Node 18+ first
npm install -g @anthropic-ai/claude-code
claude
```

Claude Code reads `CLAUDE.md` automatically, and picks up `.claude/settings.json`,
`.claude/commands/` and `.claude/agents/` from the repository.

## 3. The session loop

Paste these, in order.

**First session — orientation:**

```
/bootstrap-session
```

It will read the plan, run the tests, and tell you the state of the project without
changing anything.

**Then, to build:**

```
/run-phase 0
```

…and when that reports done:

```
/run-phase 1
```

`/run-phase N` works through every remaining task in that phase without stopping
between them. Use `/next-task` instead when you want to inspect one task at a time.

**Suggested order, with roughly what each phase gets you:**

| Command | You end up with |
|---|---|
| `/run-phase 0` | config loading, structured logging, error translation |
| `/run-phase 1` | real keyboard/browser/media/window actions, full command table, working `--dry-run` |
| `/run-phase 2` | mic → wake word → VAD → whisper → action, fully offline |
| `/run-phase 3` | Groq/Qwen handling unfamiliar phrasing, with the circuit breaker |
| `/run-phase 4` | tray app, settings, status indicator, diagnostics |
| `/run-phase 5` | the plugin system with enforced permissions |
| `/run-phase 6` | the 150-command accuracy harness, benchmarks, Windows build |

**After any phase that touched `safety/`, `llm/`, `nlu/`, `actions/` or `plugins/`:**

```
/safety-review
```

**Any time something feels off:**

```
/verify
```

## 4. One thing only you can do

Task **T-028** is marked 🔴 and blocks Phase 3. The model ID in the blueprint
(`qwen/qwen3.8-27b`) could not be verified against Groq's live catalogue when this
scaffold was written, and my own knowledge of that catalogue ends in May 2026. Run:

```powershell
python scripts\check_groq_connection.py --list
```

Then either confirm the ID, or set the correct one in `config/default.yaml` and `.env`,
and record what you found in `docs/adr/0002-groq-model-id.md`. Everything else in Phase 3
is written to be model-agnostic, so this is a one-line change — but do it before you
trust the cloud path.

## 5. Working well with Claude Code

- **Let it run.** `/run-phase` is built to go task to task without asking. Interrupting
  mid-task tends to leave a half-written test.
- **Use `/clear` between phases.** Each phase is self-contained; a fresh context window
  produces better code than a 200k-token one.
- **Read `docs/plan/PROGRESS.md`** to see what happened while you were away.
- **If it drifts**, say so plainly: *"re-read CLAUDE.md §2 and check your last change
  against invariant 3."* The invariants are numbered so you can point at them.
- **Don't let it widen before it works.** If it starts creating empty modules for a later
  phase, stop it and point at CLAUDE.md §9.
