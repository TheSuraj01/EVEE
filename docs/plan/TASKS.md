# TASKS.md — the backlog

Work top to bottom. Pick the lowest-numbered unchecked task whose dependencies are
all checked. Tick the box only when the full gate is green
(`pytest && ruff check . && ruff format --check . && mypy src`).

Legend: `[ ]` todo · `[x]` done · **AC** = acceptance criteria · **Dep** = depends on

Tasks marked 🔴 require the user (see CLAUDE.md §5). Do not silently work around them.

---

## Phase 0 — Foundation (scaffold is partly done; finish it)

- [x] **T-001** Repo skeleton, `pyproject.toml`, ruff/mypy/pytest config, CI workflow.
- [x] **T-002** `core/models.py` — `Risk`, `CommandSpec`, `IntentProposal`, `ResolvedCommand`, `ExecutionResult`.
- [x] **T-003** `core/interfaces.py` — Protocols: `SpeechProvider`, `IntentProvider`, `ActionHandler`, `WakeWordDetector`, `Clock`.
- [x] **T-004** `engine/command_registry.py` + tests.
- [x] **T-005** `nlu/alias_matcher.py` + `speech/text_normalizer.py` + tests.
- [x] **T-006** `safety/policy_engine.py` (allowlist, risk, arg bounds) + tests.
- [x] **T-007** `engine/deduplicator.py` (injectable clock) + tests.
- [x] **T-008** `engine/dispatcher.py` wiring resolve → policy → execute + integration test.

- [x] **T-009** `settings/schema.py` + `settings/loader.py`: load `config/default.yaml`,
      merge `config/user.yaml` if present, apply `VOICE_AGENT__*` env overrides, validate
      with Pydantic. Secrets never read from YAML.
      **AC**: invalid YAML raises `ConfigError` with the offending key path; env override
      of `llm.timeout_seconds` proves precedence; round-trip test on the shipped default file.
- [x] **T-010** `observability/logging.py`: structured logging + `RedactionFilter` that
      scrubs anything matching an API-key pattern and, when `privacy.redact_sensitive_logs`,
      truncates transcripts to 32 chars.
      **AC**: a log record containing a fake `gsk_...` key renders as `gsk_***`. Dep: T-009.
- [x] **T-011** `core/exceptions.py` hierarchy + `engine/result_handler.py` mapping each
      exception to a user-facing `ExecutionResult` with a safe message.
      **AC**: no exception type reaches the UI layer untranslated.

## Phase 1 — Deterministic command engine (no AI, no mic)

- [x] **T-012** `actions/keyboard.py`: `KeyboardActions` behind an injectable `KeySender`
      protocol; real impl wraps `pyautogui`/`pywin32`, test impl records calls.
      **AC**: `ui.exit_fullscreen` sends Escape; tests use the fake and never import pyautogui.
- [x] **T-013** `actions/browser.py` (`browser.next_tab`, `browser.previous_tab`,
      `browser.close_tab`→risk=medium, `browser.reopen_tab`). Dep: T-012.
- [x] **T-014** `actions/media.py` (`media.play_pause`, `media.seek_forward|backward` with
      `argument_seconds` bounded 1–600, `media.volume_up|down|mute`). Dep: T-012.
- [x] **T-015** `actions/windows.py` via pywin32: `window.minimize`, `window.maximize`,
      `window.focus_next`, `window.close` (risk=high → confirmation).
      **AC**: a `WindowBackend` protocol so tests run on non-Windows CI. Dep: T-012.
- [x] **T-016** `config/commands.yaml` — the full built-in command table (intent, aliases,
      risk, args) and a loader that registers them into `CommandRegistry` at startup.
      **AC**: every intent in the enum used by the LLM schema exists in the registry; a test
      asserts the two sets are identical (this test guards the whole safety model). Dep: T-004.
- [x] **T-017** `safety/confirmation.py`: confirmation broker for `risk >= HIGH` with a
      pluggable prompt (console in CLI mode, dialog in UI mode) and a 10 s timeout → deny.
- [x] **T-018** `safety/audit.py`: append-only JSONL audit of every decision
      (timestamp, transcript hash, intent, source local|llm, decision, reason). No raw transcript
      when redaction is on. Dep: T-010.
- [x] **T-019** `main.py --dry-run` CLI: type a phrase, see resolution + policy decision +
      simulated execution. **This is the first demoable milestone.** Dep: T-008, T-016.

## Phase 2 — Voice pipeline (local only)

- [x] **T-020** `audio/device_manager.py`: enumerate input devices, pick default, handle
      hot-unplug with a clear error. **AC**: fake backend in tests.
- [x] **T-021** `audio/ring_buffer.py`: fixed-capacity float32 ring buffer, lock-free enough
      for a callback thread. **AC**: overflow drops oldest; property test on ordering.
- [x] **T-022** `audio/capture.py` + `audio/audio_worker.py`: sounddevice stream into the ring
      buffer on a dedicated thread; start/stop/mute; never blocks the main loop. Dep: T-020/21.
- [x] **T-023** `audio/vad.py`: energy+WebRTC VAD gate producing an utterance when silence
      exceeds `silence_timeout_ms`, hard-capped by `command_timeout_seconds`.
      **AC**: deterministic test over a synthetic waveform fixture.
- [x] **T-024** `wakeword/openwakeword_adapter.py` + `wakeword/activation_controller.py`
      state machine `IDLE → LISTENING → CAPTURING → PROCESSING → COOLDOWN`.
      **AC**: cooldown prevents a second activation inside `cooldown_ms`; state machine unit-tested
      with no audio at all.
- [x] **T-025** `speech/faster_whisper_provider.py`: CPU/int8, model from config, lazy load,
      warm-up on start, 2 s guard on empty audio. **AC**: provider conforms to `SpeechProvider`;
      a `FakeSpeechProvider` is used everywhere in tests.
- [x] **T-026** `speech/transcription_service.py`: utterance → normalized transcript, with
      latency metric emitted.
- [x] **T-027** Wire the live pipeline into `main.py` (non-dry-run) behind `--no-llm`.
      **Milestone: voice controls the PC with zero cloud calls.** Dep: T-019…T-026.

## Phase 3 — Groq / Qwen intent resolution

- [x] 🔴 **T-028** Confirm the Groq model ID. Run `scripts/check_groq_connection.py --list`,
      compare against `llm.model` in `config/default.yaml`, record the outcome in
      `docs/adr/0002-groq-model-id.md`. **Blocks T-029.** See CLAUDE.md §5.
- [x] **T-029** `llm/groq_qwen_provider.py`: strict `json_schema` response format, enum built
      **from the registry at runtime** (not hand-copied), timeout + 1 retry, typed errors.
      **AC**: unit tests use a fake transport; zero network in CI. Dep: T-016, T-028.
- [x] **T-030** `llm/prompts.py`: system prompt with the command vocabulary, the active app
      context, and an explicit "choose `system.no_action` if unsure" rule. Versioned constant.
- [x] **T-031** `llm/fallback.py` + `nlu/router.py`: local alias match first; LLM only on miss;
      on LLM failure return `no_action` with a user-visible reason.
      **AC**: a test proves the provider is **not called** for a known alias. Dep: T-029.
- [x] **T-032** `nlu/confidence_policy.py`: reject proposals that are low-confidence or whose
      arguments fall outside the registry bounds; log the rejection. Dep: T-006.
- [x] **T-033** Rate limiting + circuit breaker for Groq (N calls/min, open after 3 consecutive
      failures, half-open probe). **AC**: breaker tested with a fake clock. Dep: T-029.

## Phase 4 — Desktop experience

- [x] **T-034** `ui/application.py` + `ui/tray.py`: PySide6 tray, menu (Listen/Mute, Settings,
      Logs, Quit), runs the agent on a worker thread.
- [x] **T-035** `ui/status_indicator.py`: always-visible mic state + emergency stop hotkey
      (default `Ctrl+Alt+Space`). **Required by invariant 8.**
      Phase 1 audit note: `ConfirmationBroker.cancel()` exists, but a stop pressed just
      before a prompt is lost (each `confirm()` resets it), and nothing checks for a stop
      after confirmation or inside `KeyboardActions`' press loop (up to 120 presses). Use a
      Dispatcher-owned cancel generation checked at each of those points.
      Since Phase 2: `ActivationController.mute()/unmute()` and its `on_mute` hook (cancels a
      pending confirmation) exist. Since Phase 3: `Dispatcher.dispatch(still_wanted=...)`
      re-checks before prompting and after confirmation. Still open: the press loop, and a
      stop pressed just before a prompt opens.
- [x] **T-036** `ui/settings_window.py`: device, wake word, STT model, Groq on/off, API key
      entry → Windows Credential Manager via `settings/secrets.py`.
- [x] **T-037** `storage/preferences.py` + `storage/command_history.py` (SQLite, history off
      by default, one-click purge).
- [x] **T-038** `observability/diagnostics.py`: "copy diagnostics" producing a redacted report
      (versions, devices, last 20 decisions, latency percentiles).

## Phase 5 — Plugins & security

- [x] **T-039** `plugins/api.py` + `plugins/manifest.py`: the public `Plugin` ABC and the
      manifest Pydantic schema (`specs/plugin-manifest.schema.json` is the contract).
- [x] **T-040** `plugins/loader.py` + `plugins/validator.py`: discover `plugins/*/plugin.yaml`,
      validate, reject on duplicate intent or undeclared permission, load in a try/except so one
      bad plugin cannot stop startup.
- [x] **T-041** `plugins/permissions.py`: host-side enforcement — a plugin that declares only
      `keyboard.shortcut` is denied a filesystem action **at execution time**. Dep: T-006.
- [x] **T-042** Port browser/media/window-management to real plugins; keep the registry as the
      single source of truth. Dep: T-013…T-015, T-040.
- [x] **T-043** `docs/plugin-development.md` + a working example plugin + its test.

## Phase 6 — Quality & release

- [x] **T-044** `tests/fixtures/command_corpus.yaml`: 150+ utterances — exact phrases,
      paraphrases, ambiguous, out-of-scope, adversarial ("delete all my files"), background speech.
      **AC**: a scored harness reports accuracy, false-accept and false-reject rates.
- [x] **T-045** `scripts/benchmark_intents.py` + `scripts/benchmark_stt.py`: latency percentiles
      per stage; fails CI if local path p95 > 400 ms (excluding STT model load).
- [x] **T-046** Soak test: 1 000 simulated commands, assert no leak (>50 MB growth), no duplicate
      executions, no thread left running.
- [x] **T-047** `scripts/build_windows.ps1`: PyInstaller one-dir build, no key baked in, version
      stamp, smoke test on a clean profile.
- [x] **T-048** Finish `docs/`: architecture, setup-windows, privacy, security-model,
      command-reference, troubleshooting. Update README with real screenshots/GIF.
- [x] **T-049** Release workflow: tag → build → checksum → GitHub release with a privacy
      summary in the notes.

---

## Backlog (not scheduled)

- Hermes integration as an **optional** post-hoc layer, never on the real-time path.
- Local LLM fallback (llama.cpp, 1–3 B) for offline ambiguity resolution.
- Multi-language wake words; per-application command profiles.
