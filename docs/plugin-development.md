# Writing a plugin

A plugin adds voice commands to Voice PC Agent. It is a folder with two files: a manifest
that **declares** what the plugin does, and Python code that says **how** to do it.
The host decides **whether** a command runs: the policy engine, confirmation and the
emergency stop all apply to plugin commands exactly as they do to built-in ones.

The browser, media and window-management commands are themselves plugins
(`plugins/browser`, `plugins/media`, `plugins/window-management`), so they are good
references alongside the example below.

## Quick start: the zoom plugin

`plugins/zoom` is a complete, tested example. It adds "zoom in", "zoom out" and
"reset zoom", and it ships disabled.

```
plugins/
  zoom/
    plugin.yaml    # the manifest: what the plugin declares
    plugin.py      # the code: one Plugin class
```

**plugin.yaml**

```yaml
id: zoom                 # must equal the folder name
name: Zoom
version: 1.0.0
description: Zoom the current page or document in, out, or back to 100%.
entrypoint: plugin.py    # a file in this folder; paths are refused

permissions:
  - keyboard.shortcut    # the only capability this plugin needs

commands:
  - intent: zoom.in
    description: Zoom in             # shown to the user when the command runs
    risk: low
    aliases: [zoom in, increase zoom, enlarge text]
  - intent: zoom.out
    description: Zoom out
    risk: low
    aliases: [zoom out, decrease zoom, shrink text]
  - intent: zoom.reset
    description: Reset the zoom to 100%
    risk: low
    aliases: [reset zoom, normal size, actual size]
```

**plugin.py**

```python
from voice_agent.plugins.api import ActionHandler, Plugin, PluginHost, Shortcut


class ZoomPlugin(Plugin):
    def handlers(self, host: PluginHost) -> dict[str, ActionHandler]:
        return host.shortcuts(
            {
                "zoom.in": Shortcut(("ctrl", "add")),
                "zoom.out": Shortcut(("ctrl", "subtract")),
                "zoom.reset": Shortcut(("ctrl", "0")),
            }
        )
```

**Enable it** in `config/user.yaml`. The list *replaces* the default one, so keep the
plugins you already use:

```yaml
plugins:
  enabled: [browser, media, window-management, zoom]
```

**Try it** without a microphone:

```
python -m voice_agent.main --dry-run
> zoom in
  resolved:  zoom.in (alias, confidence 1.00)
  policy:    allow (ok)
  simulated: ctrl+add
  result:    success — Zoom in
```

## The manifest

`specs/plugin-manifest.schema.json` is the published contract, and the host validates
every manifest against an equivalent model before any plugin code runs.

| Field | Rules |
|---|---|
| `id` | `^[a-z][a-z0-9-]{1,31}$`, and equal to the folder name |
| `name` | up to 64 characters |
| `version` | `MAJOR.MINOR.PATCH` |
| `description`, `author` | optional |
| `entrypoint` | a bare file name like `plugin.py` (default); no paths |
| `permissions` | unique values from the table below |
| `commands` | at least one; each intent appears once |

Each command:

| Field | Rules |
|---|---|
| `intent` | `area.action`, lowercase, e.g. `zoom.in`. Must not exist already. The areas `ui`, `system`, `browser`, `media` and `window` are reserved for the agent and its own plugins |
| `description` | up to 160 characters; this is what the user sees after it runs |
| `risk` | `low`, `medium` or `high`. `high` asks the user for a Yes first (unless they turned `safety.require_confirmation_for_destructive_actions` off) |
| `aliases` | the phrases that trigger it locally, with no cloud call. Must not be taken already |
| `argument` | optional, for a number of seconds: `{name, required, minimum, maximum, default}`, with `0 ≤ minimum ≤ default ≤ maximum ≤ 3600` |

Choose `risk` honestly. Anything that closes, deletes or cannot be undone is `high`. The
host checks the risky calls it can recognise (see [Permissions](#permissions)), but it
can't see everything your code does, so the risk you declare is what keeps an accidental
trigger from doing damage.

## The plugin API

Everything public is in `voice_agent.plugins.api`.

`Plugin.handlers(host)` returns one executor per intent in your manifest, and only those.
Executors for intents you didn't declare, or a declared intent without one, get the plugin
rejected.

`PluginHost` is everything you are given:

| Member | What it is |
|---|---|
| `host.shortcuts({intent: Shortcut})` | Ready-made executors that send key chords |
| `host.window_actions()` | Executors for `window.minimize`, `window.maximize`, `window.close` |
| `host.keys` | `hotkey(*keys)` sends a chord, `press(*keys)` a sequence (pyautogui key names) |
| `host.windows` | `minimize()`, `maximize()`, `close()` on the focused window |
| `host.interrupted()` | True once the user hit the emergency stop or muted: stop what you're doing |

`host.keys` and `host.windows` work **only inside `execute()`**, while the agent is running
one of your own commands, on the thread that called you. Using them from `handlers()`,
from a thread you started, or after the user has stopped raises an error. Nothing your
plugin does can happen without a voice command the policy engine approved, and the
emergency stop applies to it too.

`Shortcut(keys, repeat=1, seconds_per_press=None)`: `repeat` presses the chord several
times. `seconds_per_press` turns a command's seconds argument into a number of presses,
rounded up and capped at 120. `host.shortcuts()` already stops between presses when
`interrupted()` turns true.

For anything a shortcut table can't express, write an executor yourself. It is any object
with an `execute` method:

```python
from voice_agent.plugins.api import ExecutionResult, ExecutionStatus, ResolvedCommand


class CopyAndPaste:
    def __init__(self, host):
        self._host = host

    def execute(self, command: ResolvedCommand) -> ExecutionResult:
        for chord in (("ctrl", "c"), ("ctrl", "v")):
            if self._host.interrupted():
                return ExecutionResult(status=ExecutionStatus.DENIED, message="Stopped.")
            self._host.keys.hotkey(*chord)
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            intent=command.spec.intent,
            message=command.spec.description,
        )
```

`command.argument` is the number of seconds, if your command takes one. By the time your
code runs it has already been checked against your manifest's `minimum`/`maximum`.
Executors never receive what the user said, only the intent.

If your executor raises, the user sees "The action could not be completed." and the agent
carries on. The error is logged with its type.

## Permissions

The host checks **every call your code makes on a capability**. A call that needs a
permission you didn't declare raises `PermissionDenied`, and the user is told the action
isn't permitted.

| Permission | Allows |
|---|---|
| `keyboard.shortcut` | `host.keys.hotkey(...)` with Ctrl, Alt or Win, or a non-typing key such as an arrow, Esc or F11 |
| `keyboard.type` | `host.keys.press(...)`, and any chord without Ctrl/Alt/Win that types: `("x",)`, `("shift", "a")`, `("enter",)` |
| `media.control` | `host.keys.hotkey(...)` when every key is a media key (`playpause`, `volumeup`, `volumedown`, `volumemute`, `nexttrack`, `prevtrack`, `stop`) |
| `window.manage` | every `host.windows` call |
| `clipboard.read`, `clipboard.write` | reserved; no clipboard capability exists yet |

Some calls also need the **command** to be declared risky enough, so the user was asked
first:

| Call | Needs `risk` |
|---|---|
| `host.windows.close()`, Alt+F4, Ctrl+Q, Ctrl+Shift+W, Delete, Shift+Delete | `high` |
| Ctrl+W | `medium` or `high` |

There is no file, network or process capability. If your plugin needs one, it does not
belong in this design yet; open an issue.

## What the host does for you

- **Policy:** unknown intents, low confidence and out-of-range arguments are refused
  before your code is called. `high` risk asks the user first, and silence means no.
- **Local first:** your aliases are matched on the machine. Unfamiliar phrasings may go to
  the cloud model, which can only ever choose an intent that is registered, yours included.
- **No double triggers:** a repeat of the same command within about a second is ignored.
- **Audit:** every decision about your commands is logged like any other.

## Aliases must be distinguishable

A misheard phrase is matched to the closest alias above a similarity of 0.88. If your alias
sits that close to another command's, the agent could run the wrong one. The contract test
`test_aliases_of_different_commands_are_not_fuzzy_neighbours` checks every plugin in the
repository, enabled or not. The zoom plugin avoids "make it bigger" for exactly this
reason: it is a near neighbour of "make this bigger" (maximise).

## When a plugin is not loaded

Each plugin loads on its own. If yours fails, it is skipped and everything else starts
normally. The reason appears:

- at startup, as "Plugin 'name' was not loaded: …", printed in the console or shown as a
  notification in tray mode;
- in **Copy diagnostics** (tray menu), under *Plugins*;
- in the log file.

| Reason | Fix |
|---|---|
| `not found` | The folder name in `plugins.enabled` doesn't exist under `plugins/` |
| `invalid manifest (…)` | The listed field breaks a rule in the tables above |
| `manifest id … must match its folder name` | Rename the folder or the `id` |
| `expected exactly one Plugin subclass` | Define one class that subclasses `Plugin` |
| `executors for undeclared intents` / `no executor for declared intents` | Make `handlers()` and the manifest list the same intents |
| `duplicate intent` / `alias … already maps to` | Another command already owns that intent or phrase |
| `… is in the reserved 'window' namespace` | Pick an area for your intents that isn't reserved |
| `PermissionDenied: … acted outside its own command` | `handlers()` used `host.keys`/`host.windows`; only `execute()` may |
| any exception from your code | Raised while importing your module or calling `handlers()` |

## Testing

Plugins are tested without a keyboard, a window or a microphone. Load yours through the
real loader with recording fakes. `tests/integration/test_example_plugin.py` is the
template:

```python
from tests.conftest import RecordingKeySender, RecordingWindowBackend

from voice_agent.core.models import ResolvedCommand
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.execution_context import ExecutionContext
from voice_agent.plugins.loader import PLUGINS_DIR, load_plugins
from voice_agent.plugins.permissions import plugin_host


def test_zoom_in_sends_ctrl_plus() -> None:
    keys, registry, context = RecordingKeySender(), CommandRegistry(), ExecutionContext()
    handlers, report = load_plugins(
        PLUGINS_DIR,
        ["zoom"],
        registry,
        host_for=lambda manifest: plugin_host(manifest, keys, RecordingWindowBackend(), context),
    )
    assert report.rejected == {}

    spec = registry.require("zoom.in")
    with context.running(spec):  # what the dispatcher does around every executor
        handlers["zoom.in"].execute(ResolvedCommand(spec=spec))
    assert keys.calls == [("ctrl", "add")]
```

This goes through manifest validation and the permission and risk checks, so a test that
passes here won't be refused at startup, or at run time, for those reasons.

## Security: what the checks do and don't do

The checks above keep a well-meaning plugin from overreaching by mistake, and they make
its behaviour predictable. They are **not a sandbox**. A plugin is Python running inside
the agent, so a plugin written to misbehave can do anything the agent itself can:
- read files and use the network;
- reach the Groq API key and any stored command history;
- work around the wrappers in `PluginHost`.

Your aliases and descriptions also become part of the text sent to the cloud model, so
they can steer which registered command it picks for an unfamiliar phrase.

The real security boundary is therefore **which plugins you enable**. A folder dropped
into `plugins/` is discovered but never executed until someone adds it to
`plugins.enabled`. Read a plugin's code before you enable it, as you would before
installing any program.
