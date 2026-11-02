# ADR 0004 — A checked text argument: apps by name, websites by address

**Status:** ACCEPTED (user decision, 2026-10-21)

## Context

The user asked for "open chrome", "open VS Code", and "open YouTube in another tab",
using the cloud model for the last. Until now a proposal carried an intent and at most one
bounded number (`argument_seconds`): nothing could name an app or a site.

## Decision

`IntentProposal.argument_text` (at most 300 characters), with `CommandSpec.text` saying
what it may be. The policy engine checks it, the executor gets only the checked value:

- **`choice`** (`app.open`): one of a fixed list, the apps in the Start menu read at
  startup (uninstallers excluded). The executor maps the name to the shell's own app ID
  and launches that: no path, argument or command ever comes from speech or the model.
  Locally, "open/launch/start/run {}" with the Start name or an everyday one ("chrome",
  "vs code") resolves without the cloud (invariant 3).
- **`url`** (`web.open`): any **public https** address, checked by
  `safety/validation.py`. Chosen by the user over a fixed site list ("any website,
  checked"). Opened in the browser in front when it is a known browser (the address starts
  with `https://`, so it cannot be a command-line switch), otherwise the default browser.

`system.sleep` is high risk, so it is always confirmed. Modern Standby PCs refuse
`SetSuspendState`; there the executor uses Windows' Sleep menu (Win+X, U, S).

## Consequences

- The model's output space grows from "an intent and a number" to "an intent, a number and
  a checked text". The worst unconfirmed outcome becomes opening an installed app or a
  public website the user did not ask for.
- Prompt V4 (2026-10-22) no longer lists the Start-menu apps: V3's list made each request
  ~2500 tokens against Groq's 8000-a-minute free tier, so most commands came back 429. The
  model now names the app as it knows it, and the router maps that name onto the Start
  menu with the speech rules (`AliasMatcher.choice_for`) before the policy checks it.
- The JSON schema gains `argument_text`; prompt V3 explains it (V2 kept for bisecting).
