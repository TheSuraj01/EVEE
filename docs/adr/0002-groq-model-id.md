# ADR 0002 — Groq model selection

**Status:** ACCEPTED — `qwen/qwen3.8-27b` verified against the live catalogue (T-028)
**Date:** 2026-10-19 (proposed), 2026-10-20 (verified)

## Context

The project blueprint specifies the Groq model ID `qwen/qwen3.8-27b`.

**This ID has not been verified against Groq's live catalogue.** The scaffold author's
knowledge of that catalogue ends in May 2026, and at that point the Qwen model offered on
Groq was a different identifier. Model IDs on hosted platforms are renamed, deprecated and
retired on short notice, so this is exactly the kind of fact that must be checked against
the live API rather than assumed.

Consequences of getting it wrong are cheap but annoying: every cloud call returns a
404-style model error, and because the router degrades gracefully, the symptom is "the
agent quietly stopped understanding paraphrases" rather than a crash.

## Decision

Use `qwen/qwen3.8-27b` with strict `json_schema` output. Verified 2026-10-20 (below).

Verification procedure, to repeat whenever Groq announces a deprecation:

1. Run `python scripts/check_groq_connection.py --list` with a valid `GROQ_API_KEY`.
2. Pick the model that is (a) present in the catalogue, (b) documented as supporting
   **strict structured outputs / JSON schema**, and (c) fast enough that the round trip
   stays under ~900 ms p95 at `max_tokens=192`.
3. Record here: the date checked, the exact ID, whether strict JSON schema is supported,
   and the measured latency.
4. Update `llm.model` in `config/default.yaml` and `GROQ_MODEL` in `.env.example`.

## Guard rails regardless of the answer

- The model ID is **config, never a constant in code**. Nothing but configuration changes
  when it is renamed.
- `scripts/check_groq_connection.py` fails loudly if the configured ID is absent from the
  catalogue — run it in CI as a nightly, not a blocking, job.
- If strict JSON schema turns out to be unsupported for the chosen model, fall back to
  `response_format={"type": "json_object"}` plus Pydantic validation plus the registry
  membership check. **The membership check in `GroqQwenIntentProvider._parse` is what
  actually makes this safe — the schema is only an optimisation.**

## Record of verification

| Date | Checked by | Catalogue ID used | Strict JSON? | p95 latency |
|---|---|---|---|---|
| 2026-10-20 | Suraj Kumar Yadav (`check_groq_connection.py`) | `qwen/qwen3.8-27b` | Yes — strict `json_schema` round trip accepted | 157 ms, single call (p95 not yet measured) |

Catalogue on that date (11 models): allam-2-7b, canopylabs/orpheus-arabic-saudi,
canopylabs/orpheus-v1-english, meta-llama/llama-prompt-guard-2-22m,
meta-llama/llama-prompt-guard-2-86m, openai/gpt-oss-120b, openai/gpt-oss-20b,
openai/gpt-oss-safeguard-20b, qwen/qwen3.8-27b, whisper-large-v3, whisper-large-v3-turbo.

Still open:

- **p95 latency** — run `check_groq_connection.py --intent "go to the tab on the right" --repeat 20`
  and add a row. The 157 ms figure used a one-field schema; the production request carries
  the full prompt and the four-field schema.
- **`llm.reasoning_effort`** is not sent: it is unverified for this model, and the measured
  round trip is already fast without it. Verify before enabling.
