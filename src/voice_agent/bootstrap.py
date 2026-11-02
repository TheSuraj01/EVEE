"""Wires the object graph. One place to see how the pieces fit together.

Deliberately plain: no DI framework, no decorators, no import-time side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from voice_agent.actions.apps import APP_OPEN, AppActions, app_open_spec
from voice_agent.actions.keyboard import UI_SHORTCUTS, KeyboardActions
from voice_agent.actions.power import SYSTEM_SLEEP, PowerActions
from voice_agent.actions.web import WEB_OPEN, WebActions
from voice_agent.core.interfaces import (
    ActionHandler,
    Clock,
    ConfirmationPrompt,
    KeySender,
    WindowBackend,
)
from voice_agent.core.models import (
    NO_ACTION_INTENT,
    ExecutionResult,
    IntentProposal,
    PluginLoadReport,
)
from voice_agent.engine.catalog import load_command_specs
from voice_agent.engine.command_registry import CommandRegistry
from voice_agent.engine.deduplicator import Deduplicator
from voice_agent.engine.dispatcher import Dispatcher
from voice_agent.engine.execution_context import ExecutionContext
from voice_agent.llm.circuit_breaker import GuardedIntentProvider
from voice_agent.llm.groq_qwen_provider import ChatTransport, GroqQwenIntentProvider
from voice_agent.nlu.alias_matcher import AliasMatcher
from voice_agent.nlu.router import IntentRouter
from voice_agent.plugins.loader import PLUGINS_DIR, load_plugins
from voice_agent.plugins.permissions import plugin_host
from voice_agent.safety.audit import AuditLog
from voice_agent.safety.confirmation import ConfirmationBroker
from voice_agent.safety.policy_engine import PolicyEngine, PolicySettings
from voice_agent.settings.paths import AUDIT_NAME, data_dir
from voice_agent.settings.schema import Settings

#: Per-user, not the working directory: the tray app can be started from anywhere.
AUDIT_FILE = data_dir() / "audit" / AUDIT_NAME


@dataclass(frozen=True)
class SystemSeams:
    """The OS seams behind opening apps and websites and sleeping the PC. The defaults do
    nothing, for tests and the dry run; the live app passes the real ones."""

    #: Start-menu name -> the shell's app ID. No apps: no app.open command at all.
    apps: Mapping[str, str] = field(default_factory=dict)
    launch_app: Callable[[str], None] = lambda _app_id: None
    open_address: Callable[[str], None] = lambda _address: None
    suspend: Callable[[], bool] = lambda: False
    pause: Callable[[float], None] = lambda _seconds: None
    english_ui: Callable[[], bool] = lambda: True


@dataclass
class Agent:
    registry: CommandRegistry
    router: IntentRouter
    policy: PolicyEngine
    dispatcher: Dispatcher
    #: Held so the emergency stop can cancel a pending confirmation (invariant 8).
    confirmation: ConfirmationBroker | None
    #: Which plugins run and which were refused — for the UI and diagnostics.
    plugins: PluginLoadReport

    def handle(
        self, transcript: str, still_wanted: Callable[[], bool] | None = None
    ) -> ExecutionResult:
        """Transcript -> result. Unrecognised speech goes through as no_action, so it is
        refused by the policy engine and audited like every other decision. `still_wanted`
        is re-checked after the (possibly slow) cloud resolution — see Dispatcher.dispatch."""
        proposal = self.router.resolve(transcript) or IntentProposal(
            intent=NO_ACTION_INTENT, raw=transcript
        )
        result = self.dispatcher.dispatch(proposal, still_wanted)
        if self.router.last_error is not None and result.detail.get("reason") == "no_action":
            # Invariant 4: the cloud failed, nothing fired, and the user is told why.
            result = result.model_copy(update={"message": self.router.last_error})
        return result

    def knows(self, transcript: str) -> bool:
        """A command the local matcher recognises. Never asks the cloud: phrase mode uses it
        while asleep, when nothing may leave the PC."""
        return self.router.local(transcript) is not None


def builtin_handlers(
    keys: KeySender,
    interrupted: Callable[[], bool] = lambda: False,
    seams: SystemSeams | None = None,
) -> dict[str, ActionHandler]:
    """Executors for the agent's own commands (config/commands.yaml, plus app.open when
    there are apps). Everything else comes from plugins; a contract test holds the union
    equal to the registry."""
    seams = seams or SystemSeams()
    keyboard = KeyboardActions(keys, UI_SHORTCUTS, interrupted)
    handlers: dict[str, ActionHandler] = dict.fromkeys(UI_SHORTCUTS, keyboard)
    handlers[WEB_OPEN] = WebActions(seams.open_address)
    handlers[SYSTEM_SLEEP] = PowerActions(
        keys, seams.suspend, seams.pause, interrupted, english=seams.english_ui
    )
    if seams.apps:
        handlers[APP_OPEN] = AppActions(seams.apps, seams.launch_app)
    return handlers


def build_agent(
    settings: Settings,
    *,
    keys: KeySender,
    windows: WindowBackend,
    prompt: ConfirmationPrompt | None = None,
    audit_path: Path = AUDIT_FILE,
    llm: ChatTransport | None = None,
    interrupted: Callable[[], bool] = lambda: False,
    plugins_dir: Path = PLUGINS_DIR,
    clock: Clock | None = None,
    seams: SystemSeams | None = None,
) -> Agent:
    """Assemble the pipeline from config. The OS seams and the cloud transport are injected
    by the caller; without a transport (or with llm.enabled false) it is local only."""
    seams = seams or SystemSeams()
    registry = CommandRegistry()
    registry.register_all(load_command_specs())
    if seams.apps:
        registry.register(app_open_spec(seams.apps))

    safety, privacy = settings.safety, settings.privacy
    policy_settings = PolicySettings(
        reject_unknown_intents=safety.reject_unknown_intents,
        require_confirmation_for_destructive_actions=(
            safety.require_confirmation_for_destructive_actions
        ),
        minimum_confidence=safety.minimum_confidence,
    )
    context = ExecutionContext(interrupted)
    plugin_executors, plugin_report = load_plugins(
        plugins_dir,
        settings.plugins.enabled,
        registry,
        host_for=lambda manifest: plugin_host(
            manifest, keys, windows, context, policy_settings.granted_permissions
        ),
    )
    policy = PolicyEngine(registry, policy_settings)
    provider = (
        GuardedIntentProvider(
            GroqQwenIntentProvider(
                registry, llm, model=settings.llm.model, max_retries=settings.llm.max_retries
            ),
            max_per_minute=settings.llm.max_calls_per_minute,
            failures_to_open=settings.llm.circuit_breaker_failures,
        )
        if llm is not None
        else None
    )
    router = IntentRouter(
        AliasMatcher(registry, settings.nlu.fuzzy_threshold),
        llm_provider=provider,
        llm_enabled=settings.llm.enabled,
    )
    confirmation = ConfirmationBroker(prompt, windows) if prompt is not None else None
    audit = (
        AuditLog(
            audit_path,
            include_transcript=privacy.store_transcripts and not privacy.redact_sensitive_logs,
        )
        if privacy.audit_log_enabled
        else None
    )
    dispatcher = Dispatcher(
        registry,
        policy,
        Deduplicator(settings.nlu.dedupe_window_ms, clock),
        confirmation=confirmation,
        audit=audit,
        confirmation_timeout_s=safety.confirmation_timeout_seconds,
        context=context,
    )
    executors = {**builtin_handlers(keys, interrupted, seams), **plugin_executors}
    for intent, handler in executors.items():
        dispatcher.register_handler(intent, handler)

    return Agent(registry, router, policy, dispatcher, confirmation, plugin_report)
