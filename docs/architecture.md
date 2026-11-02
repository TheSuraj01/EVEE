# Architecture

## The shape of the problem

A voice agent has one dangerous property: **an untrusted, lossy input channel is wired
directly to privileged actions.** Background TV audio, a misheard word, or a model
hallucination can all become a keystroke. Every structural decision here follows from
narrowing that channel.

## Layers

```
┌──────────────────────────── ui/ (PySide6 tray) ─────────────────────────────┐
│  status indicator · mute · emergency stop · settings · diagnostics          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ ExecutionResult only
┌──────────────────────────────── engine/ ────────────────────────────────────┐
│  registry · dispatcher · deduplicator · execution context · result handler  │
└───────┬───────────────┬────────────────┬───────────────────┬────────────────┘
        │               │                │                   │
   ┌────▼────┐    ┌─────▼─────┐    ┌─────▼─────┐      ┌──────▼──────┐
   │  nlu/   │    │   llm/    │    │  safety/  │      │  actions/   │
   │ alias   │    │ groq qwen │    │  policy   │      │ keyboard    │
   │ router  │    │ prompts   │    │ perms     │      │ windows     │
   └────┬────┘    └─────┬─────┘    │ confirm   │      │ ▲ plugins/  │
        │               │          │ audit     │      │   guarded   │
        │               │          └─────┬─────┘      └──────┬──────┘
        └───────────────┴────────────────┴───────────────────┘
                                 │
                            ┌────▼────┐
                            │  core/  │  models · interfaces · exceptions
                            └─────────┘  (depends on nothing)
```

Dependency direction is one-way. `tests/integration/test_contracts.py` enforces the two
rules that matter most: `core` imports nothing from the project, and `actions` never
imports `llm`.

Browser, media and window commands are plugins (`plugins/`). Their executors never get
the real keyboard or window backend, only wrappers (`plugins/permissions.py`) that
check, on every call, that one of the plugin's own approved commands is running on
that thread, that the user hasn't stopped, and that the permission and risk were
declared. See [security-model.md](security-model.md).

## The audio path

```
sounddevice callback thread (copies samples, or drops them while muted)
  └─▶ ring_buffer (10 s, lock-free, overwrites oldest)
        └─▶ audio worker thread, 30 ms frames ─▶ wake word (openWakeWord, 80 ms chunks)
              └─▶ activation controller: IDLE → LISTENING → CAPTURING → PROCESSING → COOLDOWN
                    └─▶ VAD closes the utterance on silence_timeout_ms
                          └─▶ faster-whisper (CPU, int8) → transcript
```

The callback never blocks: it only copies into the ring buffer. Everything after it runs
on the audio worker thread, Whisper included. Audio that arrives while a command is being
processed is drained during the cooldown and ignored, so the next command starts clean.

## The decision path

```
transcript
  ├─ normalize()                     lowercase, de-punctuate, strip wake word + fillers
  ├─ AliasMatcher.match()            exact → duration-stripped → conservative fuzzy (≥0.88)
  │    └─ hit ⇒ IntentProposal(source=alias, confidence=1.0)    ← no network
  └─ miss ⇒ GroqQwenIntentProvider.propose()
       ├─ enum built from CommandRegistry.intents() at call time
       ├─ strict json_schema, temperature 0, 4 s timeout, 1 retry
       ├─ at most 20 calls a minute; 3 failures in a row ⇒ skipped for 30 s
       └─ failure ⇒ system.no_action + a reason the user sees; agent stays up
                      │
                      ▼
              PolicyEngine.evaluate()
                ├─ system.no_action      → DENY
                ├─ unknown intent        → DENY
                ├─ below min confidence  → DENY
                ├─ ungranted permission  → DENY
                ├─ argument out of range → DENY
                ├─ risk ≥ HIGH           → CONFIRM
                └─ otherwise             → ALLOW
                      │
                      ▼
              Dispatcher: still wanted? → confirm → still wanted? → dedupe
                          → execute inside ExecutionContext → audit log
```

## Why the enum is generated, not written

A hand-maintained list in the prompt is a list that drifts. When it drifts, either the
model proposes intents the host can't run (noisy failures) or the host gains commands the
model can never reach (silent dead code). Generating it from the registry makes drift
impossible, and `test_llm_enum_equals_the_registry_allowlist` fails loudly if anyone
reintroduces a copy.

## Threading model

| Thread | Work | Rule |
|---|---|---|
| Audio callback (PortAudio) | copy samples into the ring buffer | no locks, no I/O, no logging |
| `audio-worker` | wake word, VAD, STT, routing, policy, dispatch | owns all blocking work |
| `voice-service` | model warm-up, open the microphone, retry it when lost | never runs a command |
| Qt main | tray, indicator, Yes/No dialog | UI only; results arrive via a signal |
| Hotkey | `RegisterHotKey` message loop for the emergency stop | only calls `emergency_stop()` |

Mute and the emergency stop work from any thread, mid-command (invariant 8). They drop
audio in the callback, move the state machine to IDLE and cancel an open Yes/No
question. The dispatcher re-checks "still wanted?" after a slow cloud call and after
confirmation, and key loops such as a long seek check before every press.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| Mic unplugged or silent for 2 s | indicator turns red ("Microphone offline"), devices re-scanned every 2 s |
| Speech longer than `command_timeout_seconds` (8 s) | capture is cut there and transcribed |
| Whisper fails | "I couldn't transcribe that."; the loop carries on |
| Groq timeout / 5xx | one retry, then `system.no_action` with a visible reason; breaker opens after 3 |
| Groq 4xx (bad key, rate limit) | not retried; same visible reason |
| Model returns invalid JSON | rejected, treated as no match |
| Model proposes an unknown intent | rejected by the provider, and again by policy |
| Action raises, or returns something other than a result | contained in the dispatcher, becomes `ExecutionResult(status=error)` |
| A plugin fails to load | that plugin is skipped and reported; the rest start |

Every one of these resolves to **no action**, never a guessed action.

## Performance budget (i7, 16 GB, CPU only)

| Stage | Target p95 | Measured p95 |
|---|---|---|
| Wake word, per frame | < 5 ms | run `benchmark_stt.py` |
| faster-whisper `base.en` int8, short command | 300–600 ms | run `benchmark_stt.py` |
| Alias match (resolve) | < 2 ms | 1.3 ms |
| Policy + dispatch + audit write | < 20 ms | 0.4 ms |
| **Transcript to action, local** | **< 400 ms** (CI gate) | **1.6 ms** |
| Groq round trip | 300–900 ms | 157 ms, one call; p95 pending ([ADR 0002](adr/0002-groq-model-id.md)) |

Measured over the 183-utterance command corpus × 20 on the development PC, with simulated
keys. `tests/integration/test_benchmarks.py` fails CI if the local path's p95 exceeds
400 ms. `scripts/benchmark_stt.py` times the speech stages on a machine that has the
models.
