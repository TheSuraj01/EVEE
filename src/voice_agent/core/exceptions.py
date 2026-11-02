"""Exception hierarchy. Every exception the pipeline raises descends from VoiceAgentError
so the result handler can translate it into a user-safe ExecutionResult (see T-011)."""

from __future__ import annotations


class VoiceAgentError(Exception):
    """Base class for all expected failures."""

    user_message = "Something went wrong."


class ConfigError(VoiceAgentError):
    user_message = "The configuration file is invalid."


class AudioDeviceError(VoiceAgentError):
    user_message = "The microphone is unavailable."


class TranscriptionError(VoiceAgentError):
    user_message = "I couldn't transcribe that."


class IntentResolutionError(VoiceAgentError):
    user_message = "I couldn't work out what you meant."


class LLMUnavailableError(IntentResolutionError):
    """Groq timed out, errored, or returned an unusable payload."""

    user_message = "The online intent model is unavailable; local commands still work."


class PolicyViolation(VoiceAgentError):
    user_message = "That action is not permitted."


class PermissionDenied(PolicyViolation):
    user_message = "That component is not allowed to perform this action."


class Interrupted(PolicyViolation):
    """The user stopped (mute / emergency stop) while an action was under way."""

    user_message = "Stopped."


class ActionExecutionError(VoiceAgentError):
    user_message = "The action could not be completed."


class PluginError(VoiceAgentError):
    user_message = "A plugin failed to load."
