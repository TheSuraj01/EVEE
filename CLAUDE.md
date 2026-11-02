# CLAUDE.md — Operating manual for Claude Code on this repository

You are the primary engineer on **Voice PC Agent**. Read this file fully before your
first edit in any session. It outranks your defaults. If something here conflicts with
a user instruction in the chat, the user wins — but say so out loud.

---

## 1. What this project is

An offline-first **voice control agent for Windows 11**. The user speaks; the app
performs a desktop action (press Escape, next tab, seek forward, focus a window).

Hardware target: Intel i7, 16 GB RAM, **no dedicated GPU**. Everything local must run
on CPU in int8.

Split of responsibilities:

| Stage | Where it runs |
|---|---|
| Wake word | Local (openWakeWord) |
| VAD + capture | Local (sounddevice + webrtcvad-style gate) |
| Speech-to-text | Local (faster-whisper, CPU, int8) |
| Known command lookup | Local, deterministic, **no network** |
| Ambiguous phrasing → intent | Groq cloud API (Qwen), strict JSON schema |
| Policy / permission decision | **Always local. Never the model.** |
| Action execution | Local (pywin32 / pyautogui) |

### The one rule that defines the architecture

> **The model proposes. The local policy engine disposes.**

The LLM may only return an `intent` string from a fixed enum plus bounded arguments.
It never returns code, shell, key sequences, or file paths. If an intent is not in the
registry, it is rejected — there is no "best effort" path.

---

## 2. Non-negotiable invariants

Treat a violation of any of these as a build failure, not a style opinion.

1. **No arbitrary execution.** Never `eval`, `exec`, `os.system`, `subprocess(shell=True)`
   on any value derived from a transcript or an LLM response. Ever.
2. **Allowlist only.** `PolicyEngine.evaluate()` must be the single gate before
   `Dispatcher` executes. No action module is called directly from `nlu/` or `llm/`.
3. **Known commands bypass the LLM.** If `AliasMatcher` resolves the transcript, the
   Groq client is not called. Pressing a key must never require the internet.
4. **Degrade, don't die.** Groq unreachable / timeout / malformed JSON ⇒ fall back to
   local alias matching and surface a non-fatal status. The agent stays usable.
5. **Privacy by default.** Audio is never written to disk. Transcripts are not persisted
   unless `privacy.store_transcripts: true`. Only the transcript + a tiny context blob
   is sent to Groq — never raw audio.
6. **No secrets in code, config, logs or artifacts.** `GROQ_API_KEY` comes from the
   environment or Windows Credential Manager. `config/*.yaml` is committed; `.env` is not.
7. **Typed boundaries.** Every cross-module payload is a Pydantic model from
   `core/models.py`. No bare dicts across package boundaries.
8. **Kill switch.** Mute and emergency-stop must work even while an action is pending.
9. **Idempotence window.** The deduplicator must block a repeated identical intent inside
   `dedupe_window_ms`. Double wake-word triggers are expected, not exceptional.

---

## 3. How you must work (process)

### Start of every session
1. Read `docs/plan/TASKS.md`.
2. Pick the **lowest-numbered unchecked task whose dependencies are all checked**.
3. Announce: task ID, what you'll change, how you'll prove it works.

### For every task
1. Write or update the test **first** (`tests/unit/...` or `tests/integration/...`).
2. Implement the smallest change that satisfies the acceptance criteria.
3. Run the gate: `python -m pytest -q && ruff check . && ruff format --check . && mypy src`
4. Fix until green. Do not proceed with a red gate.
5. Tick the checkbox in `docs/plan/TASKS.md` and append one line to `docs/plan/PROGRESS.md`
   (`T-012 | done | added policy risk gating | tests/unit/test_policy.py`).
6. Commit: `git add -A && git commit -m "feat(safety): <summary> [T-012]"`.
7. Move to the next task without asking permission. Only stop for the reasons in §5.

### Commit convention
Conventional Commits, scoped by package, with the task ID in brackets:
`feat(nlu): alias matcher with fuzzy fallback [T-008]`

---

## 4. Decisions already made — do not relitigate

- Python **3.12**. Not 3.13 (wheel availability for audio/ML deps on Windows).
- Package manager: `uv` preferred, `pip` acceptable. `src/` layout, importable as
  `voice_agent`.
- Validation: **Pydantic v2**. Config: YAML + Pydantic schema, env overrides via
  `VOICE_AGENT__SECTION__KEY`.
- UI: **PySide6** system tray first; the main window is secondary.
- Tests: pytest. Audio and network are **always faked in tests** — no test may open a
  microphone or hit api.groq.com.
- Logging: structured, `observability/logging.py`, with a redaction filter. No `print()`
  in `src/`.
- Lint/format: `ruff`. Types: `mypy --strict` on `src/voice_agent/core` and `safety`,
  normal mode elsewhere.

---

## 5. When to stop and ask the user

Stop for these. Keep going for everything else.

- You need a real `GROQ_API_KEY`, or a live-API decision (see `docs/adr/0002-groq-model-id.md`
  — **the configured model ID is unverified and must be confirmed against the live
  catalogue before Phase 3 can close**).
- A task requires installing something outside `pyproject.toml`.
- An invariant in §2 appears to block a required feature.
- You are about to delete or rewrite more than ~100 lines you did not write this session.
- A Windows-only API cannot be tested in the current environment and you must decide
  between a stub and a hard dependency.

Otherwise: pick the next task and continue.

---

## 6. Repository map (what goes where)

```
src/voice_agent/
  core/        models, protocols, exceptions, constants — depends on nothing
  audio/       capture, device selection, ring buffer, VAD
  wakeword/    openWakeWord adapter + activation state machine
  speech/      faster-whisper provider, transcription service, text normalizer
  nlu/         alias matcher, router (local-first), confidence policy
  llm/         Groq Qwen provider, prompts, strict schema, retries, fallback
  engine/      command registry, dispatcher, dedupe, execution context, results
  safety/      policy engine, permissions, confirmation, validation, audit log
  actions/     keyboard, windows, media, browser, accessibility executors
  plugins/     plugin API, manifest schema, loader, validator, permission mapping
  settings/    config schema + loader + secrets + paths
  storage/     preferences, command history
  ui/          PySide6 tray, windows, status indicator
  observability/ logging, metrics, diagnostics
```

**Dependency direction is one-way:**
`ui → engine → {nlu, llm, safety, actions} → core`.
`core` imports nothing from the project. `actions` never imports `llm`. If you need the
reverse direction, you need an interface in `core/interfaces.py`, not an import.

---

## 7. Definition of done for any module

- Public functions have type hints and a one-line docstring saying *why*, not *what*.
- Failure modes are explicit: timeout, empty input, device missing, malformed response.
- Unit test covers happy path + at least one failure path.
- No network, no mic, no sleep > 50 ms in tests.
- `ruff`, `mypy`, `pytest` all green.

## 8. Useful commands

```bash
uv sync                      # or: pip install -e ".[dev]"
python -m pytest -q
python -m pytest -q tests/unit/test_policy.py -k reject
ruff check . && ruff format .
mypy src
python scripts/check_groq_connection.py     # needs GROQ_API_KEY
python -m voice_agent.main --dry-run        # pipeline without mic/LLM
```

## 9. Anti-patterns seen in projects like this — avoid

- Calling the LLM for "next tab". It is a dictionary lookup.
- A `utils.py` that becomes a dumping ground. Name modules by responsibility.
- Swallowing exceptions to keep the voice loop alive. Catch narrowly, log, emit a
  `ExecutionResult(status=error)`, keep looping.
- Creating all 70 files as empty stubs. **Build vertically**: a thin working path first,
  then widen. A module should not exist until a task needs it.
- Trusting `plugin.yaml` permissions. The host enforces; the manifest only declares.
