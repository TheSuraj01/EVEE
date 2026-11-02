"""What the agent is doing, as words and a colour. Qt-free, so the wording is tested in CI."""

from __future__ import annotations

from voice_agent.wakeword.activation_controller import State

GREEN, AMBER, BLUE, GREY, RED = "#2e7d32", "#f9a825", "#1565c0", "#9e9e9e", "#c62828"
INDIGO = "#5c6bc0"

_ACTIVE = {
    State.LISTENING: ("Listening", GREEN),
    State.COOLDOWN: ("Listening", GREEN),
    State.CAPTURING: ("Hearing you", AMBER),
    State.PROCESSING: ("Working", BLUE),
}


def describe(
    state: State, muted: bool, running: bool, loading: bool = False, asleep: bool = False
) -> tuple[str, str]:
    """(label, colour). Idle has four causes the user must be able to tell apart."""
    if muted:
        return "Muted", GREY
    if loading:  # the first run downloads the models: minutes, and the mic isn't open yet
        return "Loading models", GREY
    if asleep and state is not State.IDLE:
        # Phrase mode: speech is checked for the wake phrase, so it is captured and
        # transcribed, but flashing 'Hearing you' for every sentence in the room would be noise.
        return "Asleep", INDIGO
    if state in _ACTIVE:
        return _ACTIVE[state]
    if running:
        return "Microphone offline", RED
    return "Stopped", GREY
