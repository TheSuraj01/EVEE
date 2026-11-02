"""Translates any exception into an ExecutionResult the UI can show.

Only the class-level `user_message` ever reaches the user — never `str(exc)`, which can
carry a transcript, a path or a key. The exception itself goes to the redacted log.
"""

from __future__ import annotations

import logging

from voice_agent.core.exceptions import PolicyViolation, VoiceAgentError
from voice_agent.core.models import NO_ACTION_INTENT, ExecutionResult, ExecutionStatus

log = logging.getLogger(__name__)


def to_result(
    exc: Exception,
    intent: str = NO_ACTION_INTENT,
    fallback: type[VoiceAgentError] = VoiceAgentError,
) -> ExecutionResult:
    """Keep the voice loop alive without leaking internals; `fallback` words unknown errors."""
    if isinstance(exc, VoiceAgentError):
        log.warning("%s: %s", type(exc).__name__, exc)
        message = exc.user_message
    else:
        log.error("unexpected %s", type(exc).__name__, exc_info=exc)
        message = fallback.user_message
    status = ExecutionStatus.DENIED if isinstance(exc, PolicyViolation) else ExecutionStatus.ERROR
    return ExecutionResult(
        status=status, intent=intent, message=message, detail={"reason": type(exc).__name__}
    )
