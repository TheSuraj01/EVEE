"""Entry point.

`--dry-run` is the development loop: type a phrase, watch it travel through the real
router, the real policy engine and the real dispatcher, with execution simulated. It needs
no microphone, no model and no API key, which makes it the fastest way to test behaviour.

With no flag it is the live pipeline: microphone -> wake word -> VAD -> Whisper -> router
(local aliases first, Groq only on a miss) -> policy -> keyboard/window actions.
`--no-llm` keeps it entirely on this machine. Ctrl+C stops it.

`--self-test` checks an install offline (the Windows build's smoke test). The packaged
build is windowed, with no console, so there it opens the tray unless told otherwise.
Only one agent runs per Windows session: two would both hear, and both act on, every command.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from voice_agent import __version__
from voice_agent.bootstrap import AUDIT_FILE, build_agent
from voice_agent.core.exceptions import ConfigError
from voice_agent.core.interfaces import ConfirmationPrompt
from voice_agent.core.models import NO_ACTION_INTENT, ExecutionResult, IntentProposal
from voice_agent.observability.logging import configure_logging
from voice_agent.safety.confirmation import ConsolePrompt
from voice_agent.service import build_live_service
from voice_agent.settings.loader import load_settings
from voice_agent.settings.schema import Settings

#: Win32 ERROR_ALREADY_EXISTS, from CreateMutexW when another agent holds the name.
_ALREADY_EXISTS = 183
#: One agent per Windows logon session ("Local\" scope, not machine-wide).
INSTANCE_MUTEX = "Local\\VoicePCAgent"
_held_mutexes: list[int] = []


class Simulator:
    """KeySender + WindowBackend for --dry-run: records what would happen, touches nothing."""

    def __init__(self) -> None:
        self.actions: list[str] = []

    def press(self, *keys: str) -> None:
        self.actions.extend(keys)

    def hotkey(self, *keys: str) -> None:
        self.actions.append("+".join(keys))

    def foreground(self) -> int:
        return 1

    def activate(self, handle: int) -> bool:
        return handle == self.foreground()

    def minimize(self) -> None:
        self.actions.append("minimise foreground window")

    def maximize(self) -> None:
        self.actions.append("maximise foreground window")

    def close(self) -> None:
        self.actions.append("close foreground window (WM_CLOSE)")

    def drain(self) -> str:
        """Summarise and forget: 'right x6' rather than six lines."""
        counts = Counter(self.actions)
        self.actions.clear()
        return ", ".join(f"{a} x{n}" if n > 1 else a for a, n in counts.items())


def run_dry_run(
    settings: Settings,
    *,
    prompt: ConfirmationPrompt | None = None,
    audit_path: Path = AUDIT_FILE,
) -> int:
    """REPL over the real pipeline; only the OS seams are simulated."""
    sim = Simulator()
    agent = build_agent(
        settings, keys=sim, windows=sim, prompt=prompt or ConsolePrompt(), audit_path=audit_path
    )

    print(f"Voice PC Agent — dry run. {len(agent.registry)} commands loaded.")
    print("Type a phrase, or 'quit'.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.lower() in {"quit", "exit"}:
            return 0
        if not line:
            continue

        proposal = agent.router.resolve(line)
        if proposal is None:
            print("  resolved:  nothing — I didn't recognise that command.")
            # Still dispatched, so the refusal goes through policy and into the audit log.
            proposal = IntentProposal(intent=NO_ACTION_INTENT, raw=line)
        else:
            argument = (
                f", {proposal.argument_seconds}s" if proposal.argument_seconds is not None else ""
            )
            print(
                f"  resolved:  {proposal.intent} ({proposal.source.value}, "
                f"confidence {proposal.confidence:.2f}{argument})"
            )

        verdict = agent.policy.decide(proposal)  # display only; dispatch() evaluates
        print(f"  policy:    {verdict.decision.value} ({verdict.reason})")
        result = agent.dispatcher.dispatch(proposal)
        simulated = sim.drain()
        if simulated:
            print(f"  simulated: {simulated}")
        print(f"  result:    {result.status.value} — {result.message}")


def run_live(settings: Settings, use_llm: bool) -> int:  # pragma: no cover - mic, models
    """Console front end for VoiceService. Ctrl+C stops it."""
    service = build_live_service(
        settings,
        use_llm=use_llm,
        prompt=ConsolePrompt(),
        notify=print,
        on_result=_print_result,
        on_wake=lambda: print("  (listening for a command)"),
    )
    from voice_agent.ui.hotkey import start_emergency_hotkey  # stdlib ctypes only; no Qt

    wake = settings.wakeword
    hint = f"Say '{wake.model.replace('_', ' ')}', then" if wake.enabled else "Speak"
    stop_key = settings.app.emergency_stop_hotkey
    hotkey, notice = start_emergency_hotkey(stop_key, service.emergency_stop)
    active = stop_key if notice is None else "the tray"
    if notice is not None:
        print(f"WARNING: {notice}" + ("" if hotkey else " Use Ctrl+C to stop."))
    print(f"Voice PC Agent. {hint} a command. Ctrl+C: quit.")
    if notice is None:
        print(f"Emergency stop: {active}.")
    service.start()
    told_muted = False
    try:
        while service.running:
            service.join(0.5)  # short joins keep Ctrl+C responsive on Windows
            told_muted = console_mute_step(service, _key_nowait, told_muted)
    except KeyboardInterrupt:
        pass
    finally:
        if hotkey is not None:
            hotkey.stop()
        service.stop()
        print("Stopped.")
    return 0


class _Mutable(Protocol):
    @property
    def muted(self) -> bool: ...

    def unmute(self) -> None: ...


def console_mute_step(service: _Mutable, key: Callable[[], str | None], told_muted: bool) -> bool:
    """One pass of the console loop's way back from Mute or the emergency stop: U listens
    again. Returns whether the user has been told it is muted. On every new mute (including
    one right after an unmute) whatever was typed before is discarded first, so the kill
    switch is undone by a deliberate press and nothing else. No key is read while listening:
    those belong to a Yes/No question."""
    if not service.muted:  # read once per pass: the drain must precede any key read
        return False
    if not told_muted:
        drain_keys(key)
        print("Muted. Press U to listen again.")
    return not console_unmute(service, key)


def console_unmute(service: _Mutable, key: Callable[[], str | None]) -> bool:
    """U unmutes; True if it did. Only call after drain_keys() on the mute edge."""
    if service.muted and (key() or "").lower() == "u":
        service.unmute()
        print("Listening again.")
        return True
    return False


def drain_keys(key: Callable[[], str | None], limit: int = 100_000) -> None:
    """Discard everything already typed: None means the buffer is empty ('' is a key that
    isn't a character). Bounded only as a guard; the loop outruns any key auto-repeat."""
    for _ in range(limit):
        if key() is None:
            return


def _key_nowait() -> str | None:  # pragma: no cover - needs a real console
    if sys.platform != "win32":
        return None  # the console app targets Windows; elsewhere unmute from the tray
    import msvcrt

    if not msvcrt.kbhit():
        return None
    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):  # arrow/function keys come as two codes; the second can be 'u'
        msvcrt.getwch()
        return ""  # a key, just not a character: never None, which would end a drain early
    return key


def ensure_std_streams() -> None:
    """The packaged app is windowed: no console, so sys.stdout and sys.stderr are None.
    Libraries that report progress (the first-run Whisper and wake-word downloads use tqdm)
    would crash writing to them, so give them somewhere harmless to write."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, Path(os.devnull).open("w", encoding="utf-8"))  # noqa: SIM115


def claim_single_instance(name: str | None = None) -> bool:
    """True when no other agent runs in this Windows session. Holds a named mutex until this
    process exits; Windows releases it even after a crash."""
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    handle = kernel32.CreateMutexW(None, False, name or INSTANCE_MUTEX)
    if not handle:
        return True  # can't tell: never block startup on it
    if ctypes.get_last_error() == _ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _held_mutexes.append(handle)
    return True


def _tell_user(message: str) -> None:
    """stderr, plus a dialog in the packaged app, which has no console to print to."""
    print(message, file=sys.stderr)
    if getattr(sys, "frozen", False) and sys.platform == "win32":  # pragma: no cover - dialog
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Voice PC Agent", 0x40)


def run_self_test(settings: Settings, check_imports: bool = True) -> int:
    """Offline install check: no microphone, model, key or network. Plugins load, a command
    runs through the real pipeline (execution simulated), and the libraries that live voice,
    the cloud and the tray need all import — a build missing a DLL fails here, not later."""
    checks: list[tuple[str, str | None]] = []  # (what, failure)
    sim = Simulator()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        agent = build_agent(settings, keys=sim, windows=sim, audit_path=Path(tmp) / "audit.jsonl")
        rejected = "; ".join(f"{name}: {why}" for name, why in agent.plugins.rejected.items())
        checks.append((f"plugins: {', '.join(agent.plugins.loaded)}", rejected or None))
        result = agent.handle("next tab")
        sent = sim.drain()
        checks.append(("'next tab' -> ctrl+tab", None if sent == "ctrl+tab" else result.message))
    if check_imports:
        checks += _library_checks()

    print(f"Voice PC Agent {__version__} self-test")
    for what, failure in checks:
        print(f"  FAIL  {what}: {failure}" if failure else f"  ok    {what}")
    failed = any(failure for _, failure in checks)
    print("self-test FAILED" if failed else "self-test passed")
    return 1 if failed else 0


def _library_checks() -> list[tuple[str, str | None]]:
    """Plain imports, one group per feature. OSError: a native library failed to load."""
    checks: list[tuple[str, str | None]] = []
    try:  # what the live path imports, including the first-run model downloaders
        import ctranslate2  # noqa: F401
        import faster_whisper  # noqa: F401
        import huggingface_hub  # noqa: F401
        import onnxruntime  # noqa: F401
        import openwakeword.model
        import openwakeword.utils  # noqa: F401
        import sounddevice  # noqa: F401
        import webrtcvad  # noqa: F401
    except (ImportError, OSError) as exc:
        checks.append(("voice libraries", str(exc)))
    else:
        checks.append(("voice libraries", None))
    try:
        import groq  # noqa: F401
    except ImportError as exc:
        checks.append(("cloud library", str(exc)))
    else:
        checks.append(("cloud library", None))
    try:
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([])  # loads Qt's Windows platform plugin
    except (ImportError, OSError) as exc:
        checks.append(("tray library", str(exc)))
    else:
        checks.append(("tray library", None))
    if sys.platform == "win32":
        try:
            import pyautogui  # noqa: F401
            import pythoncom  # noqa: F401  (opening apps and websites)
            import win32com.client  # noqa: F401  (reading the Start menu)
            import win32cred  # noqa: F401
            import win32gui  # noqa: F401
            import win32timezone  # noqa: F401  (reading a saved key: see secrets.py)
        except (ImportError, OSError) as exc:
            checks.append(("Windows libraries", str(exc)))
        else:
            checks.append(("Windows libraries", None))
    return checks


def _print_result(transcript: str, result: ExecutionResult) -> None:
    heard = f"heard '{transcript}' -> " if transcript else ""
    print(f"  {heard}{result.status.value}: {result.message}")


def main(argv: list[str] | None = None) -> int:
    ensure_std_streams()
    parser = argparse.ArgumentParser(prog="voice-agent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--self-test", action="store_true", help="check this install offline, then exit"
    )
    parser.add_argument("--dry-run", action="store_true", help="type phrases instead of speaking")
    parser.add_argument(
        "--no-llm", action="store_true", help="live voice, local only: never contact the cloud"
    )
    parser.add_argument("--tray", action="store_true", help="run in the system tray")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        _tell_user(f"Configuration error: {exc}")
        return 2
    configure_logging(
        redact_transcripts=settings.privacy.redact_sensitive_logs,
        store_transcripts=settings.privacy.store_transcripts,
    )

    if args.self_test:
        return run_self_test(settings)
    if args.dry_run:
        return run_dry_run(settings)
    if not claim_single_instance():
        _tell_user("Voice PC Agent is already running: look for its dot in the notification area.")
        return 1
    if args.tray or getattr(sys, "frozen", False):  # the packaged build has no console
        try:
            from voice_agent.ui.application import run_tray
        except ImportError:
            print('The tray needs PySide6: pip install -e ".[ui]"', file=sys.stderr)
            return 2
        return run_tray(settings, use_llm=not args.no_llm)
    return run_live(settings, use_llm=not args.no_llm)


if __name__ == "__main__":
    sys.exit(main())
