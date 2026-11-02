# ADR 0003 — The policy engine is the only gate

**Status:** accepted · **Date:** 2026-10-19

## Context

Three different components can produce something that looks like a command: the alias
matcher, the cloud model, and (later) plugins. If each one validated itself, there would be
three places for a bug to let an action through, and a fourth the day someone adds a
hotkey path.

## Decision

`PolicyEngine.evaluate(IntentProposal) -> PolicyVerdict` is the single authorisation point.
`Dispatcher` is the only component that calls an `ActionHandler`, and it calls it only on an
`ALLOW` verdict (or a `CONFIRM` verdict the user approved).

Checks live in one place and in this order: registry membership → confidence → permissions
→ argument bounds → risk/confirmation.

## Consequences

- Reviewing the safety of this system means reading one file.
- Adding a resolver is cheap: produce an `IntentProposal`, get the same guarantees free.
- `tests/integration/test_contracts.py` enforces the structural half (no dynamic execution,
  no cross-layer imports); the rest is enforced by the dispatcher having the only reference
  to the handler map.
- Cost: the policy engine must know about arguments and permissions, which makes it the
  most heavily tested module in the project. That is the right place to spend test budget.
