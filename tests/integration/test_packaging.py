"""What the Windows build relies on: bundle-relative paths, a version stamp, and the offline
self-test that scripts/build_windows.ps1 runs as its smoke test on a clean profile."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

from voice_agent import __version__
from voice_agent.main import main, run_self_test
from voice_agent.settings.loader import load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_a_frozen_build_reads_config_commands_and_plugins_from_the_bundle(tmp_path: Path) -> None:
    probe = (
        "import sys; sys._MEIPASS = sys.argv[1]\n"
        "from voice_agent.engine.catalog import DEFAULT_COMMANDS_FILE\n"
        "from voice_agent.plugins.loader import PLUGINS_DIR\n"
        "from voice_agent.settings.loader import DEFAULT_CONFIG_DIR\n"
        "print(DEFAULT_CONFIG_DIR, DEFAULT_COMMANDS_FILE, PLUGINS_DIR, sep='\\n')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout.split()

    assert [Path(line) for line in out] == [
        tmp_path / "config",
        tmp_path / "config" / "commands.yaml",
        tmp_path / "plugins",
    ]


def test_the_package_and_pyproject_agree_on_the_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == __version__


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])

    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"voice-agent {__version__}"


def test_self_test_passes_on_a_good_install(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_self_test(load_settings(env={}), check_imports=False) == 0

    out = capsys.readouterr().out
    assert f"Voice PC Agent {__version__} self-test" in out
    assert "FAIL" not in out
    assert out.rstrip().endswith("self-test passed")


def test_self_test_fails_when_a_plugin_is_missing(capsys: pytest.CaptureFixture[str]) -> None:
    settings = load_settings(env={"VOICE_AGENT__PLUGINS__ENABLED": "['browser', 'nope']"})

    assert run_self_test(settings, check_imports=False) == 1

    out = capsys.readouterr().out
    assert "FAIL  plugins: browser: nope: not found" in out
    assert out.rstrip().endswith("self-test FAILED")


@pytest.mark.slow  # ~4 s of native imports; the release job runs it with every extra
def test_self_test_imports_every_runtime_library_when_installed(tmp_path: Path) -> None:
    """In a child process, as the build's smoke test runs it: the suite itself must never
    import pyautogui or pywin32 (see test_keyboard_actions / test_window_actions)."""
    missing = [m for m in ("win32gui", "faster_whisper", "groq", "PySide6") if not find_spec(m)]
    if missing:
        pytest.skip(f"extras not installed: {missing}")
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path)}  # logs land here, not in the profile
    env.pop("GROQ_API_KEY", None)

    run = subprocess.run(
        [sys.executable, "-m", "voice_agent.main", "--self-test"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert "FAIL" not in run.stdout


def test_a_packaged_build_opens_the_tray_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The windowed exe has no console, so the console modes would be invisible."""
    pytest.importorskip("PySide6")
    import voice_agent.main as entry
    import voice_agent.ui.application as ui

    calls: list[Any] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(ui, "run_tray", lambda settings, use_llm: calls.append(use_llm) or 0)
    monkeypatch.setattr(entry, "configure_logging", lambda **_: None)

    assert main([]) == 0
    assert calls == [True]


def test_a_windowed_build_gets_harmless_std_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """No console: sys.stdout/stderr are None, and tqdm progress bars in the first-run model
    downloads would crash writing to them."""
    from voice_agent.main import ensure_std_streams

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    ensure_std_streams()

    sys.stderr.write("progress 10%\r")  # what tqdm does
    sys.stdout.write("x")


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows named mutex")
def test_only_one_agent_runs_at_a_time() -> None:
    """Two would both hear every command and both act on it."""
    import uuid

    from voice_agent.main import claim_single_instance

    name = rf"Local\VoicePCAgent-test-{uuid.uuid4()}"

    assert claim_single_instance(name) is True
    assert claim_single_instance(name) is False


def test_console_unmute_reads_keys_only_while_muted() -> None:
    """A confirmation's 'y' must never be taken: keys are read only while muted, when no
    Yes/No question can be open."""
    from voice_agent.main import console_unmute

    class Service:
        muted = False

        def unmute(self) -> None:
            self.muted = False

    reads: list[str] = []

    def key() -> str | None:
        reads.append("read")
        return "u"

    service = Service()
    console_unmute(service, key)
    assert reads == []

    service.muted = True
    console_unmute(service, lambda: "x")
    assert service.muted

    console_unmute(service, key)
    assert not service.muted


def test_keys_typed_before_a_mute_cannot_undo_it() -> None:
    """The kill switch: 'quit' typed earlier sits in the console buffer; its 'u' must not
    unmute once the emergency stop lands. Only a U pressed after the mute counts."""
    from voice_agent.main import console_unmute, drain_keys

    class Service:
        muted = True

        def unmute(self) -> None:
            self.muted = False

    buffered = list("quit")
    service = Service()

    drain_keys(lambda: buffered.pop(0) if buffered else None)  # on the mute edge
    console_unmute(service, lambda: buffered.pop(0) if buffered else None)

    assert buffered == []
    assert service.muted


def test_draining_a_held_key_still_ends() -> None:
    from voice_agent.main import drain_keys

    drain_keys(lambda: "u")  # auto-repeat never runs dry; the drain is bounded


class _Muted:
    def __init__(self) -> None:
        self.muted = True

    def unmute(self) -> None:
        self.muted = False


def _keys(*typed: str) -> tuple[Any, list[str]]:
    buffer = list(typed)
    return (lambda: buffer.pop(0) if buffer else None), buffer


def test_an_arrow_key_in_the_buffer_does_not_stop_the_drain() -> None:
    """Up (to recall a command) then 'quit': an extended key reads as '' and the drain goes
    on, so the 'u' in 'quit' is discarded, not left to undo the emergency stop."""
    from voice_agent.main import console_mute_step

    key, buffer = _keys("", "q", "u", "i", "t")
    service = _Muted()

    told = console_mute_step(service, key, told_muted=False)  # the mute edge

    assert (told, buffer, service.muted) == (True, [], True)


def test_a_mute_right_after_an_unmute_is_drained_again() -> None:
    """'uu' typed fast: the first unmutes; a mute that lands before the next pass must not be
    undone by the second."""
    from voice_agent.main import console_mute_step

    key, buffer = _keys("u", "u")
    service = _Muted()

    told = console_mute_step(service, key, told_muted=True)  # muted earlier; this U unmutes
    assert not service.muted
    service.muted = True  # the emergency stop lands again
    told = console_mute_step(service, key, told)

    assert (told, buffer, service.muted) == (True, [], True)


def test_while_listening_the_console_reads_no_keys() -> None:
    from voice_agent.main import console_mute_step

    service = _Muted()
    service.muted = False

    assert console_mute_step(service, lambda: pytest.fail("read a key"), told_muted=True) is False
