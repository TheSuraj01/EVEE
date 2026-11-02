"""Opening installed apps: app.open, with the app named by its Start-menu name.

The apps are read from the Start menu once, at startup; what was said only ever picks one
of them by name. The launch target is the shell's own ID for the app, never a path or a
command taken from speech or from the cloud model.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Iterable, Mapping

from voice_agent.core.exceptions import ActionExecutionError
from voice_agent.core.models import (
    CommandSpec,
    ExecutionResult,
    ExecutionStatus,
    ResolvedCommand,
    Risk,
    TextArgument,
)

APP_OPEN = "app.open"
#: Uninstallers sit in the Start menu next to their apps; none is ever opened by voice.
_NEVER = ("uninstall", "deinstall", "désinstall", "desinstal", "disinstall")
#: Script and web shortcuts in the Start menu are not apps: a script runs whatever it holds,
#: and a .url skips the web-address check.
_NOT_APPS = (".bat", ".cmd", ".ps1", ".vbs", ".js", ".url")
#: A name longer than this is not something anyone says.
MAX_NAME_CHARS = 60


def start_menu_apps() -> dict[str, str]:  # pragma: no cover - the Windows shell
    """Start-menu name -> the shell's app ID, the way Start search lists them (desktop and
    Store apps alike). Empty when the shell can't be asked."""
    if sys.platform != "win32":
        return {}
    import win32com.client

    folder = win32com.client.Dispatch("Shell.Application").NameSpace("shell:AppsFolder")
    return launchable((str(item.Name), str(item.Path)) for item in folder.Items())


def launchable(apps: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Drop uninstallers, unnamed entries and absurd names; the first of a name wins."""
    found: dict[str, str] = {}
    for name, app_id in apps:
        name = name.strip()
        if not name or not app_id or len(name) > MAX_NAME_CHARS:
            continue
        if any(word in name.casefold() for word in _NEVER):
            continue
        if "://" in app_id or app_id.casefold().endswith(_NOT_APPS):
            continue  # a web shortcut (listed by its address) or a script
        found.setdefault(name, app_id)
    return found


def with_com() -> None:  # pragma: no cover - Win32
    """ShellExecute (os.startfile) wants COM on the calling thread, above all for shell:
    paths; the voice thread never set it up."""
    if sys.platform == "win32":
        import pythoncom

        with contextlib.suppress(pythoncom.com_error):  # already initialised another way
            pythoncom.CoInitialize()


def launch_app(app_id: str) -> None:  # pragma: no cover - starts a real program
    if sys.platform == "win32":
        with_com()
        os.startfile("shell:AppsFolder\\" + app_id)  # noqa: S606 - an ID the shell listed


def app_open_spec(apps: Mapping[str, str]) -> CommandSpec:
    """The registry entry: its choices are exactly the apps found, so the policy engine and
    the cloud model's list can only ever name one of them."""
    return CommandSpec(
        intent=APP_OPEN,
        description="Open an installed app",
        risk=Risk.LOW,
        permissions=("app.launch",),
        text=TextArgument(
            kind="choice",
            choices=tuple(apps),
            say=("open {}", "launch {}", "start {}", "run {}", "open the {} app"),
        ),
    )


class AppActions:
    """ActionHandler for app.open."""

    def __init__(self, apps: Mapping[str, str], launch: Callable[[str], None]) -> None:
        self._apps = dict(apps)
        self._launch = launch

    def execute(self, command: ResolvedCommand) -> ExecutionResult:
        name = command.text or ""
        app_id = self._apps.get(name)
        if app_id is None:  # the policy checked the name against the same list
            msg = f"no app called {name[:MAX_NAME_CHARS]!r}"
            raise ActionExecutionError(msg)
        try:
            self._launch(app_id)
        except OSError as exc:
            msg = f"could not open {name}: {exc.strerror or type(exc).__name__}"
            raise ActionExecutionError(msg) from exc
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS, intent=APP_OPEN, message=f"Opening {name}."
        )
