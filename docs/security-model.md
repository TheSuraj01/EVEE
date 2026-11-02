# Security model

## Trust boundaries

| Source | Trusted? | Why |
|---|---|---|
| Microphone audio | **No** | Anyone in the room, or a TV, can speak |
| Whisper transcript | **No** | Lossy; "close all" and "close call" differ by one phoneme |
| Groq / Qwen response | **No** | A remote service outside your control |
| `plugins/*/plugin.yaml` | **No** | Third-party declarations, not grants |
| `config/commands.yaml` | **Yes** | Ships with the application, reviewed in PRs |
| Policy engine verdict | **Yes** | The thing everything else is checked against |

## Attack scenarios and mitigations

**Ambient speech triggers a destructive action.**
Wake word + cooldown + VAD-bounded capture + confirmation for `risk >= HIGH`. The worst
unconfirmed outcome is a low-risk keystroke.

**Transcript collision steers to a dangerous command.**
Fuzzy matching only fires at 0.88 similarity or above, and a contract test
(`test_aliases_of_different_commands_are_not_fuzzy_neighbours`) keeps the phrases of
different commands too far apart to be mistaken for each other. A high-risk command still
asks first, however it was matched. Below the threshold the phrase goes to the model,
which can only return a registered intent, which still faces policy. The command corpus
(`tests/fixtures/command_corpus.yaml`: adversarial, out-of-scope, ambiguous and
background speech) must produce zero false accepts on the local path.

**The cloud response is attacker-controlled.**
Worst case is a valid-schema proposal for a *registered* intent with an in-range argument —
i.e. a command the user could have issued by voice anyway, and a high-risk one still needs
confirmation. The model cannot return code, a key sequence, a path, or an unregistered
intent (`_parse` rejects it; policy rejects it again).

Its one text field (ADR 0004) is checked by the policy engine like the number is: for
`app.open` it must be one of the app names read from the Start menu (the executor launches
the shell's own ID for that name, never the text), and for `web.open` a public `https://`
address (`safety/validation.py`: no other scheme, no IP or local-network name, no login
part, no port, no internationalised look-alike host, nothing but URL characters). So the
worst a hostile reply can do is open an installed app, or a public website, of its choice —
and `system.sleep` still asks first.

**Prompt injection via spoken content.**
The transcript is a user-role message with a fixed system prompt; the output space is a
closed enum, so there is nothing to inject *into*. Injection could at most cause a wrong
registered command — handled by confirmation and the deduplicator.

**A plugin escalates.**
Permissions are enforced host-side at execution time (T-041), not read from the manifest.
A plugin declaring `keyboard.shortcut` is denied a window action even if its handler tries.
Its capabilities work only inside one of its own dispatched commands, on that thread, and
never after a stop. Destructive calls also need a matching declared risk. First-party
namespaces (`ui`, `system`, `app`, `web`, `browser`, `media`, `window`...) are reserved, and
the capabilities to launch apps, open websites and sleep the PC belong to the agent alone:
a manifest cannot declare them. These are guard
rails, not a sandbox: plugins are in-process Python. The boundary is `plugins.enabled`,
and nothing outside that list is ever executed.

**Secret exfiltration.**
The key is read from the environment or Credential Manager, never logged (`RedactionFilter`),
never in diagnostics, never baked into a build: `scripts/build_windows.ps1` fails if
`src/`, `plugins/` or `config/` contain anything key-shaped before the build (PyInstaller
compresses modules, so the output scan alone can't see into them), or if the output
contains a key-shaped string, a `.env` or a `user.yaml`.

**The agent keeps listening or acting after you say stop.**
Mute and the emergency stop drop audio in the capture callback, cancel an open Yes/No
question, are re-checked by the dispatcher after slow steps, and interrupt key loops
between presses (invariant 8). A plugin's capabilities stop working at the same moment.

## Hard prohibitions

Enforced by `tests/integration/test_contracts.py::test_no_dynamic_execution_anywhere_in_src`:
no `eval`, `exec`, `compile`, `__import__`, `os.system`, `shell=True`, `pickle.load(s)`,
`marshal.loads` or `importlib.import_module` anywhere in `src/`. Loading plugin code is
confined to `plugins/loader.py` (`test_code_loading_lives_only_in_the_plugin_loader`).

`safety.allow_arbitrary_shell` and `allow_arbitrary_python` exist in the config as explicit,
permanently-false markers. If a PR sets either to true, reject it.

## Reporting

See `SECURITY.md`. Please don't open a public issue for a vulnerability.
