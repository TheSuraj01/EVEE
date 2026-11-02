"""Contract tests that guard the whole safety model. Treat a failure here as a P0."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from importlib.util import find_spec
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import FakeTransport, Shipped

from voice_agent.core.constants import BUILTIN_PERMISSIONS, KNOWN_PERMISSIONS
from voice_agent.core.models import NO_ACTION_INTENT
from voice_agent.llm.groq_qwen_provider import GroqQwenIntentProvider
from voice_agent.nlu.alias_matcher import FUZZY_THRESHOLD
from voice_agent.plugins.loader import PLUGINS_DIR
from voice_agent.speech.text_normalizer import command_key

SRC = Path(__file__).resolve().parents[2] / "src"

FORBIDDEN = (
    "os.system(",
    "shell=True",
    "pickle.loads(",
    "pickle.load(",
    "marshal.loads(",
    "importlib.import_module(",
    "builtins.eval",
    "builtins.exec",
)
#: eval/exec/__import__ with an argument, dotted or not (`__builtins__.exec(code)`), and
#: the bare `compile(` builtin. Only argument-less calls pass — Qt's `app.exec()` runs an
#: event loop, not code — and `re.compile(` is a method, not the builtin.
FORBIDDEN_BUILTINS = re.compile(r"(?<!\w)(eval|exec|__import__)\((?!\))|(?<![\w.])compile\(")


def test_llm_enum_equals_the_registry_allowlist(shipped: Shipped) -> None:
    """If these two sets ever diverge, the model can propose something unimplemented or
    the host can implement something the model cannot reach. Built-ins and plugins alike."""
    registry = shipped.agent.registry
    provider = GroqQwenIntentProvider(registry, FakeTransport(), model="test-model")
    enum = provider.response_format()["json_schema"]["schema"]["properties"]["intent"]["enum"]
    assert set(enum) == set(registry.intents())


def test_every_registered_intent_has_an_executor_and_nothing_else_does(
    shipped: Shipped,
) -> None:
    """Registered-but-unwired fails at runtime; wired-but-unregistered is unreachable code
    that a future refactor could expose without the policy engine knowing. Checked on the
    real agent, so plugin commands are held to it too."""
    agent = shipped.agent
    assert agent.dispatcher.wired_intents == set(agent.registry.intents()) - {NO_ACTION_INTENT}


def test_every_declared_permission_is_one_the_host_enforces(shipped: Shipped) -> None:
    """And the capabilities only the agent itself has (apps, websites, sleep) stay its own."""
    for spec in shipped.agent.registry:
        allowed = KNOWN_PERMISSIONS | (
            BUILTIN_PERMISSIONS if spec.plugin_id == "builtin" else set()
        )
        assert set(spec.permissions) <= allowed, spec.intent


def test_aliases_of_different_commands_are_not_fuzzy_neighbours(shipped_with: Any) -> None:
    """Otherwise a slightly mis-heard phrase could fuzzy-match the *other* command —
    e.g. 'exit full screen' landing on enter_fullscreen. Checked with EVERY plugin in the
    repo enabled: users turn plugins on later, and the guarantee must still hold."""
    every_plugin = sorted(p.parent.name for p in PLUGINS_DIR.glob("*/plugin.yaml"))
    agent = shipped_with(enabled=every_plugin).agent
    assert agent.plugins.rejected == {}
    pairs = [(command_key(a), s.intent) for s in agent.registry for a in s.aliases]
    for (a, intent_a), (b, intent_b) in combinations(pairs, 2):
        if intent_a != intent_b:
            score = SequenceMatcher(None, a, b).ratio()
            assert score < FUZZY_THRESHOLD, f"{a!r} ({intent_a}) ~ {b!r} ({intent_b}): {score}"


def test_every_registered_alias_key_is_its_own_key(shipped_with: Any) -> None:
    """The duplicate/hijack check runs on the key stored at registration; lookups key the
    phrase again. If keying a key changed it, the check would guard a different string."""
    every_plugin = sorted(p.parent.name for p in PLUGINS_DIR.glob("*/plugin.yaml"))
    registry = shipped_with(enabled=every_plugin).agent.registry
    assert [key for key in registry.aliases() if command_key(key) != key] == []


def test_no_dynamic_execution_anywhere_in_src() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                offenders.append(f"{path.relative_to(SRC)}: {needle}")
        for match in FORBIDDEN_BUILTINS.finditer(text):
            offenders.append(f"{path.relative_to(SRC)}: {match.group(0)}")
    assert offenders == [], f"dynamic execution found: {offenders}"


def test_only_the_app_and_web_executors_start_programs() -> None:
    """os.startfile (ShellExecute) runs what it is given: it may appear only where the target
    is a Start-menu ID or a policy-vetted https address."""
    allowed = {Path("voice_agent/actions/apps.py"), Path("voice_agent/actions/web.py")}
    users = {
        path.relative_to(SRC)
        for path in SRC.rglob("*.py")
        if "startfile(" in path.read_text(encoding="utf-8")
    }
    assert users == allowed


def test_actions_never_import_the_llm_layer() -> None:
    """Dependency direction from CLAUDE.md §6."""
    actions = SRC / "voice_agent" / "actions"
    for path in actions.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "voice_agent.llm" not in text, f"{path} imports the llm layer"


def test_core_depends_on_nothing_in_the_project_except_core() -> None:
    core = SRC / "voice_agent" / "core"
    for path in core.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("import voice_agent", "from voice_agent")):
                assert "voice_agent.core" in line, f"{path}: {line}"


def test_the_execution_check_still_catches_the_builtins() -> None:
    """Guard the guard: the precision fix for Qt's .exec() must not open a hole."""
    for bad in (
        "eval(x)",
        "  exec(code)",
        "y = __import__(name)",
        "(exec(c))",
        "__builtins__.exec(code)",  # Phase 4 audit: a dotted call with an argument
        "b.eval(src)",
        "compile(src, 'f', 'exec')",
    ):
        assert FORBIDDEN_BUILTINS.search(bad), bad
    for fine in ("app.exec()", "box.exec()", "cursor.execute(q)", "my_eval(x)", "re.compile(p)"):
        assert not FORBIDDEN_BUILTINS.search(fine), fine


def test_code_loading_lives_only_in_the_plugin_loader() -> None:
    """Loading code is confined to one audited place, fed only by enabled plugin folders —
    never by a transcript or a model reply."""
    loading = ("importlib.util", "exec_module", "import_module", "spec_from_file_location")
    users = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if any(api in path.read_text(encoding="utf-8") for api in loading)
    )
    assert users == [str(Path("voice_agent/plugins/loader.py"))]


def test_every_key_a_plugin_sends_is_one_pyautogui_knows(tmp_path: Path) -> None:
    """A misspelt key name ("pgdown") is silently ignored by pyautogui: the command would
    'succeed' and do nothing. Checked in a child process: the suite never imports pyautogui."""
    if find_spec("pyautogui") is None:
        pytest.skip("pyautogui not installed")
    keys = sorted(
        {key for chords in _every_shortcut(tmp_path) for chord in chords for key in chord}
    )
    probe = (
        "import json, sys, pyautogui;"
        "print(json.dumps(sorted(set(json.loads(sys.argv[1])) - set(pyautogui.KEYBOARD_KEYS))))"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe, json.dumps(keys)], capture_output=True, text=True, check=True
    )
    assert json.loads(run.stdout) == []


def _every_shortcut(tmp_path: Path) -> list[list[tuple[str, ...]]]:
    """The key chords of every command of every plugin in the repo, and the built-ins."""
    from tests.conftest import RecordingKeySender, RecordingWindowBackend

    from voice_agent.bootstrap import build_agent
    from voice_agent.core.models import IntentProposal
    from voice_agent.settings.loader import load_settings

    every = sorted(path.parent.name for path in PLUGINS_DIR.glob("*/plugin.yaml"))
    settings = load_settings(
        env={
            "VOICE_AGENT__PLUGINS__ENABLED": str(every),
            "VOICE_AGENT__NLU__DEDUPE_WINDOW_MS": "0",
            "VOICE_AGENT__SAFETY__REQUIRE_CONFIRMATION_FOR_DESTRUCTIVE_ACTIONS": "false",
        }
    )
    keys = RecordingKeySender()
    agent = build_agent(
        settings, keys=keys, windows=RecordingWindowBackend(), audit_path=tmp_path / "a.jsonl"
    )
    chords = []
    for spec in agent.registry:
        keys.calls.clear()
        agent.dispatcher.dispatch(IntentProposal(intent=spec.intent))
        chords.append(list(keys.calls))
    return chords
