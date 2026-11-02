# Troubleshooting

Start with the indicator (bottom-right of your screen, unless you dragged it). It says what the agent is
doing:

![Indicator states: Listening, Hearing you, Working, Muted, Microphone offline](img/indicator-states.png)

Two quick checks that need no microphone:

```powershell
voice-agent --self-test   # settings, plugins, one command through the pipeline, libraries
voice-agent --dry-run     # type phrases and see what each would do (nothing is pressed)
```

## Nothing happens when I speak

- **Loading models** (grey): still starting. The first run downloads the models, which
  can take a few minutes; later starts take seconds.

- **Asleep** (indigo): it only listens for "wake up ev". Say it, or click the pill. You can
  go straight on with the command ("wake up ev, open chrome"). Waking plays Windows'
  "Asterisk" sound and going to sleep its "Default Beep" (`ui.sounds: false` turns them off;
  if you hear nothing, check those two in Windows Sound settings). If it still stays asleep
  when you speak quietly or from across the room, lower `audio.vad_energy_floor` (default
  0.01) to 0.005 in your `user.yaml`: speech below it is never heard at all.
- **Listening** (green): awake, so every sentence is a command. If you chose a wake word
  instead of phrases (Settings), say "Hey Jarvis" before each command; if that rarely
  wakes it, lower `wakeword.threshold` (default 0.6)
  to 0.5 in your `user.yaml` (`config\user.yaml` from source; the packaged app's is in
  `%LOCALAPPDATA%\voice-pc-agent\`).
- **Hearing you** (amber) but nothing runs: the phrase may not be a listed command. Type it
  into `--dry-run` to see what it matches. See also "It hears me but does nothing" below.
- **Muted** (grey): click the indicator once. Mute is remembered across restarts.
- **Microphone offline** (red): the microphone is missing, unplugged or in use by another
  app. The agent retries every 2 seconds. Pick a microphone in Settings, and check
  Windows Settings → Privacy & security → Microphone → "Let desktop apps access your
  microphone".

## It hears me but does nothing, or "I didn't recognise that command"

The phrase isn't in the [command reference](command-reference.md), and the cloud is off,
has no key, or the model couldn't map it either. Either:
- use a listed phrase, or add your own as an alias (see the end of the command reference);
- turn on "Ask Groq when a phrase is not a known command" in Settings and add a key, so
  rephrasings like "go to the tab on the right" work.

## "Ignored a repeated command."

The same command came twice within 1.2 seconds. This catches a double trigger from one
utterance. If you really say the same command twice that fast, lower
`nlu.dedupe_window_ms`.

## "The online intent model is unavailable; local commands still work."

The cloud failed: you're offline, the key is wrong or missing, Groq is down, or the model
ID has changed. Listed commands are unaffected. To see which, run:

```powershell
python scripts/check_groq_connection.py
```

After 3 failures in a row the agent stops asking the cloud for 30 seconds, so a dead
network doesn't slow every command down.

"Model not found" from that script means Groq retired the model: see
[ADR 0002](adr/0002-groq-model-id.md).

## The emergency-stop hotkey doesn't work

Another program already owns that key combination. The agent then falls back to
**Ctrl+Alt+Backspace** (then Ctrl+Shift+Alt+Space) and shows a notification naming the key
that works; the console app prints it. To choose your own, set it in your `user.yaml`:

```yaml
app:
  emergency_stop_hotkey: ctrl+alt+pause
```

The tray menu's **Emergency stop** always works. In the console app, after an emergency
stop press **U** to listen again.

## "Voice PC Agent is already running"

Only one copy runs at a time, because two would both hear and both act on every command.
Look for its dot in the notification area (it may be under the ^ arrow), or quit the other
copy from its tray menu.

## A plugin wasn't loaded

The tray shows "Plugin 'name' was not loaded: reason", and **Copy diagnostics** lists it
under Plugins. Usual causes: a typo in `plugins.enabled`, a folder name that doesn't match
the manifest `id`, or a command or phrase another plugin already uses.
[plugin-development.md](plugin-development.md#when-a-plugin-is-not-loaded) explains each message.

## Commands don't work in one particular window

- **Admin windows:** Windows blocks input sent to a program running as administrator from
  one that isn't (Task Manager, installers, an elevated terminal). Avoid controlling those
  windows, or run the agent as administrator too, which gives it, and its plugins, the same
  power.
- **Wrong window:** shortcuts go to whichever window has focus. Click the window first.
- **Close, minimise and maximise** deliberately do nothing on the desktop, the taskbar, a
  console window or the agent's own windows.

## It mishears what I say

Two speech models work together. `base.en` hears everything first (about a second on a
laptop CPU). When what it heard is unclear (no command the agent knows, and Whisper was
unsure of it, or nearly the wake phrase), the same audio is heard again by `small.en`, which
copes much better with accents, speed and distance: about 3 seconds more, only then. Turn
that off with `speech.careful_model: null`, or make every command use the larger model with
`speech.model: small.en` (always ~3 s).

To see what the agent hears from *your* voice, close the agent (tray → Quit) and run:

```powershell
.venv/Scripts/python scripts/voice_check.py
```

It asks you to say a dozen commands and the wake and sleep phrases, and for each prints
what each model heard, whether the agent would have done the right thing, and how loud your
microphone is compared with `audio.vad_energy_floor`. Nothing is recorded to disk or sent
anywhere. Speak as you would to the agent.

## Transcription is slow

`speech.model: base.en` with int8 on the CPU is the default. `tiny.en` is faster but
mishears more command words; `small.en` is more accurate and roughly three times as slow.
The `.en` models only understand English; for another language pick `base` and set
`speech.language` (and set `speech.careful_model` to `small`, or `null`). To measure your
machine, record a command as a 16 kHz mono WAV and run:

```powershell
python scripts/benchmark_stt.py --wav command.wav
```

## CPU use while idle

The wake-word model checks the audio continuously, which costs a little CPU all the time.
Unticking "Listen for a wake word" in Settings removes that cost. Every utterance is then
transcribed and treated as a command, so only do this somewhere quiet.

## Something else

Tray → **Copy diagnostics** puts a report on the clipboard: versions, settings, devices,
plugins, timings and the last 20 decisions. It never includes the key or what you said.
Paste it into an issue. The full log is at
`%LOCALAPPDATA%\voice-pc-agent\logs\voice-agent.log` (tray → **Open logs**).
