# ADR 0001 — Local-first pipeline with cloud assist

**Status:** accepted · **Date:** 2026-10-19

## Context

The target machine is an i7 with 16 GB and no GPU. A local LLM capable of reliable intent
extraction would either be too slow for a sub-second interaction loop or would consume
memory the user needs for the applications being controlled. A fully cloud pipeline would
make every keystroke depend on the internet and send continuous audio off the machine.

## Decision

Split by cost and sensitivity:

- **Local:** wake word, VAD, speech-to-text, alias matching, policy, execution.
- **Cloud (optional):** interpreting phrasing that matched no alias.

Known commands never touch the network. The cloud model may only return an intent string
from a runtime-generated enum.

## Consequences

- Pressing Escape works on a plane. Paraphrases work when online.
- Two resolution paths to test — mitigated by routing both through one `IntentProposal`
  type and one policy engine.
- The command table becomes a load-bearing artifact: it is simultaneously the alias
  dictionary, the policy allowlist and the model's vocabulary. That concentration is
  deliberate; it is the only place a capability can be introduced.
- A local small-model fallback stays open as a future option without changing this split.
