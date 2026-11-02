---
description: Adversarial review of the safety and privacy model.
---

Act as a hostile security reviewer for this repository. Assume the attacker controls the
audio input and can influence the LLM response.

Answer with concrete file:line evidence, not generalities:

1. Give me three transcripts that could cause an unintended destructive action. Trace each
   through `nlu/router.py` → `safety/policy_engine.py` → `actions/*` and show where it is
   stopped. If one is not stopped, write the failing test and fix it.
2. If the Groq response were fully attacker-controlled JSON (valid against the schema),
   what is the worst reachable outcome? Is it acceptable?
3. Where could a transcript, an API key, or audio reach disk, a log, the audit file, or a
   crash report?
4. Can a plugin obtain a capability it did not declare? Prove it with a test.
5. Rank every finding P0–P3 and add P0/P1 items to `docs/plan/TASKS.md` as new tasks.
