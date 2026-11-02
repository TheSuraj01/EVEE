# Privacy

What Voice PC Agent hears, what it keeps, and what leaves your PC.

## Stays on your PC, always

- **Audio.** Microphone audio is held in a 10-second in-memory buffer that is constantly
  overwritten while listening, and zeroed when you mute. It is never written to disk and
  never uploaded. `privacy.store_audio` can
  only be `false`; the setting exists so you can see that.
- **Wake word and speech-to-text.** openWakeWord and faster-whisper run on your CPU.
- **Every listed command.** A phrase in the [command reference](command-reference.md) is
  matched on your PC and never causes a network request.
- **Waking and sleeping.** Asleep, the agent transcribes speech on your PC only to check for
  "wake up ev", alone or opening a sentence ("wake up ev, open chrome"). Anything else is
  dropped at once: it is not acted on, saved or sent anywhere, and not logged unless you
  turned on `privacy.store_transcripts`. Long stretches of
  speech (over 6 seconds) are not even transcribed. A sentence that opens with the wake
  phrase wakes the agent, and the rest of that sentence is its first command, so it is
  treated like any sentence said while awake (below).
- **Opening apps.** The list of apps in your Start menu is read on your PC, and "open
  chrome" is matched there.

## What can leave your PC

Only when **all** of these are true:
1. the agent is awake (it reacts to every sentence), and a sentence matches no listed
   command;
2. the cloud is on (`llm.enabled`, "Ask Groq when a phrase is not a known command" in
   Settings);
3. you have added a Groq API key.

Then two things go to Groq's API, over HTTPS, with your key:
- **the transcript**, cut to 500 characters;
- **the list of commands** (names, short descriptions, one example phrase each), so the
  model can pick one.

Your Start-menu apps are not sent: for "open that code editor" the model names the app as
it knows it ("Visual Studio Code"), and the agent matches that name against your Start
menu on your PC.
Sentences longer than 14 words, and the filler Whisper writes for noise ("Thank you.",
"you"), are never sent: they are not commands.

Plus what any HTTPS request to Groq carries: your IP address, and headers with the OS,
Python and Groq SDK versions. Nothing else goes: no audio, no file names, no window titles, no
browsing history.
While awake the agent treats every sentence as a command, so conversation in the room
that isn't a listed command is sent too: say "sleep ev" when you're done, or turn the
cloud off. Requests are capped at 5 a minute (Groq's free tier allows about that many). After 3
failures in a row the cloud is skipped for 30 seconds.

With the cloud off or no key, nothing leaves your PC while the agent runs, and every listed
command still works. Start the console app with `--no-llm` to force this for one session.

**First run only:** the speech model downloads once from Hugging Face (about 150 MB for
`base.en`) and the wake-word models from openWakeWord's GitHub releases. After that the agent
starts offline. The second, larger speech model (`small.en`, about 480 MB) downloads the same
way, once.

## What is written to disk

Your settings are in `config\user.yaml` when run from source; the packaged app keeps
them with everything else. They never contain the key. Everything lives in
`%LOCALAPPDATA%\voice-pc-agent\`:

| File | Contains | When |
|---|---|---|
| `logs\voice-agent.log` | Technical events, with key-shaped text masked. **No transcripts**, unless you turn on `privacy.store_transcripts`. 2 MB per file, 4 files kept | always |
| `audit\decisions.jsonl` | One line per command: time, command, where it came from (local or cloud), allowed or refused and why, and how long it took. What you said is stored only as a keyed hash; see below | unless `privacy.audit_log_enabled: false` |
| `voice-agent.sqlite3` | Whether you left the agent muted, so it restarts muted | always |
| … command history, in the same file | Time, **what you said**, the command and its result | only with `privacy.store_transcripts: true` |

About the audit hash: it uses a random key that exists only in memory and changes every
time the app starts. Within one session it shows that the same phrase came up twice.
Nobody can turn it back into words, even with a list of likely phrases, and hashes from
different sessions can't be matched up. The audit log does not rotate. It grows by about
250 bytes per command, and you can delete it at any time.

The transcript is written to the audit log in plain text only if you set both
`privacy.store_transcripts: true` and `privacy.redact_sensitive_logs: false`.

## Your controls

| You want to | Do this |
|---|---|
| Stop it listening now | Tray → **Mute**, or the emergency-stop hotkey (default Ctrl+Alt+Space; Ctrl+Alt+Backspace if another program has it) |
| Keep everything on your PC | Settings → untick "Ask Groq…", or set `llm.enabled: false` |
| Delete your saved key | Settings → **Remove key** |
| Delete command history | Tray → **Clear command history**: rows deleted and the file compacted |
| Delete everything | Quit, then delete `%LOCALAPPDATA%\voice-pc-agent\` |
| See what it would send for support | Tray → **Copy diagnostics**: versions, settings, devices, whether a key is set (never the key), plugins, timings, and the last 20 decisions, without transcripts |

**Mute** drops microphone samples inside the audio callback, so nothing you say reaches
memory while muted, and it zeroes the buffer of what was heard just before. The microphone stream itself stays open, so Windows' "microphone in
use" icon stays on. Quit the app to release the microphone. Mute is remembered: if you quit
muted, the agent starts muted next time.

## Your API key

Saved from Settings, the key goes to Windows Credential Manager, not to a file. For
development it can also come from the `GROQ_API_KEY` environment variable or a `.env` file
in the project folder. It is never written to config, logs, diagnostics or the audit log,
and the build script refuses to package anything that looks like a key, checking both the
sources before the build and the files it produces.

## Third parties

Groq, only when the cloud is used as described above. What Groq keeps is governed by
Groq's own terms. No analytics, no telemetry, no crash reporting.
