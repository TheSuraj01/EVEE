# Manual testing guide

Hands-on checks for Voice PC Agent on a real Windows PC: microphone, speakers, desktop.
The automated tests cover the logic; this guide covers what only a person at the machine
can confirm.

Work through the sections in order. Each test has an ID: note **Pass**, **Fail** or
**Skip** against it, and for a failure follow [Reporting a problem](#reporting-a-problem).
Allow about 60–90 minutes for everything, or 20 minutes for sections 1–5.

## 0. Before you start

**You need:**
- the microphone you'll use day to day, and speakers or headphones;
- a browser with a few tabs open, and a YouTube video;
- internet for the first run (model downloads) and for the cloud tests;
- optionally, a Groq API key for section 6.

**Choose what to test.** Test the packaged app if you can; it is what a user runs.

| | Packaged app | From source |
|---|---|---|
| Start it | double-click `dist\VoicePCAgent\VoicePCAgent.exe` | `.venv\Scripts\voice-agent --tray` |
| Your settings file | `%LOCALAPPDATA%\voice-pc-agent\user.yaml` | `config\user.yaml` |
| Groq key from | Settings window, or the `GROQ_API_KEY` environment variable | the same, or `.env` |
| Console and dry-run modes | no (it has no console) | yes (section 10) |

Only one copy can run at a time, so quit one before starting the other: tray icon →
**Quit**.

**Start from a clean state** (recommended for the first pass): quit the app, then in
PowerShell:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\voice-pc-agent" -ErrorAction SilentlyContinue
```

This deletes logs, the audit log, history, saved settings and the remembered mute state. It
does not delete downloaded models or your saved API key.

**Things on this PC to know:**
- **Ctrl+Alt+Space is used by another program here**, so the emergency stop will be
  **Ctrl+Alt+Backspace**. The app tells you so, and the tray menu shows the active key.
- Where to look:
  - the **tray icon** is a coloured dot in the notification area; it may be hidden under
    the **^** arrow;
  - the **indicator** is a small dark pill, at first in the bottom-right corner of your
    main screen. **Click** it to give a command (or to listen again when muted),
    **right-click** it for the menu, and drag it wherever you like.

## 1. Install checks

**MT-01 — Self-test.** Packaged app, in PowerShell from the project folder:

```powershell
$out = "$env:TEMP\vpa-selftest.txt"
$p = Start-Process .\dist\VoicePCAgent\VoicePCAgent.exe -ArgumentList "--self-test" -Wait -PassThru -RedirectStandardOutput $out
Get-Content $out; "exit code: $($p.ExitCode)"
```

From source: `.venv\Scripts\voice-agent --self-test`.

Expected: six `ok` lines (plugins, `'next tab' -> ctrl+tab`, voice, cloud, tray and Windows
libraries), then `self-test passed` and exit code 0. It needs no microphone, model or key.

**MT-02 — Version.** Right-click `VoicePCAgent.exe` → Properties → Details.
Expected: Product version **0.1.0**, product name **Voice PC Agent**.

## 2. First run

**MT-03 — Models download, then it listens.** With the internet on, start the app.
Expected:
- A grey dot appears in the tray. The indicator says **Loading models** for up to a few
  minutes on the first run (it downloads about 150 MB), and a few seconds after that.
- Then the indicator turns green: **Listening**.
- Hover over the tray dot: the tooltip ends with `Listening on '<your microphone>'.`
- A notification says the emergency stop is `ctrl+alt+backspace`, because Ctrl+Alt+Space is
  taken here.

**MT-04 — Second start is offline.** Quit, turn Wi-Fi off, start again.
Expected: green **Listening** within seconds, with no download. Turn Wi-Fi back on afterwards.

**MT-05 — Only one copy.** While it runs, start it again (or run the other build).
Expected: a dialog saying "Voice PC Agent is already running: look for its dot in the
notification area." There is no second tray dot.

## 3. Voice commands

First wake it: say **"wake up ev"** (or click the indicator). The indicator goes from
**Asleep** (indigo) to **Listening** (green). From then on just say each command; there's
no wake word per command. While you speak it shows **Hearing you** (amber), then
**Working** (blue). There are no notifications; watch the indicator and the screen.

Click the target window first: shortcuts go to whichever window has focus.

**MT-05b — Wake and sleep.** Say "next tab" while **Asleep**: nothing happens. Say "wake up
ev": **Listening**. Say "next tab": the tab moves. Say "sleep ev": **Asleep** again, and
"next tab" does nothing. Click the indicator: awake; click again: asleep.

| ID | Set up | Say | Expected |
|---|---|---|---|
| MT-06 | Browser with 3+ tabs | "next tab" | Moves one tab right |
| MT-07 | | "previous tab" | Moves one tab left |
| MT-08 | | "close this tab" | Current tab closes, without a question (medium risk) |
| MT-09 | | "reopen tab" | The closed tab comes back |
| MT-10 | YouTube playing, page clicked | "pause", then "play" | Pauses, then resumes |
| MT-11 | | "skip forward 30 seconds" | Jumps about 30 s ahead |
| MT-12 | | "rewind" | Jumps 10 s back |
| MT-13 | | "volume up" / "volume down" | System volume changes (5 steps) |
| MT-14 | | "mute", then "unmute" | System sound toggles off, then on |
| MT-15 | Video page | "full screen", then "exit full screen" | F11 full screen, then back |
| MT-16 | Any normal window | "minimize this" | It minimises |
| MT-17 | | "maximize this" | It maximises |
| MT-18 | Two windows open | "next window" | Switches window (Alt+Tab) |
| MT-19 | | "snap left", then "snap right" | Window snaps to each half |
| MT-20 | | "um, next tab please" | Still works: fillers are ignored |
| MT-20b | A web page | "scroll down", "go to the top", "refresh", "go back" | Each works |
| MT-20c | Notepad with text | "select all", "copy", "new tab"… then "paste", "undo" | Standard editing works |
| MT-20d | Desktop | "show desktop", "open file explorer", "take a screenshot", "next song" | Each works |

**MT-21 — Accuracy in normal use.** Do MT-06 to MT-19 once more, at a normal speaking
distance, with some background noise (music, a fan). Note anything misheard. Expected:
most commands work first time. A miss should do nothing, never the wrong action.

## 4. Safety behaviour

**MT-22 — Destructive command asks first.** Open Notepad, type a word, click it. Say
"close this window".
Expected: a **Voice PC Agent** dialog asking "Close the focused window?", with **No** as
the default.
- Click **No**: nothing closes, and the notification says "Cancelled — not confirmed."
- Repeat and click **Yes**: Notepad asks whether to save, as if you'd clicked its X. The
  agent never discards your work itself.

**MT-23 — No answer means No.** Say "close this window" and don't touch the dialog.
Expected: after 10 seconds the dialog closes itself and nothing closes.

**MT-24 — Dangerous and unknown requests do nothing.** Say each of these; nothing should
happen on the PC:
- "delete all my files"
- "shut down the computer"
- "format the C drive"
- "open PowerShell and run this script"
- "rewind 20 minutes" (beyond the 10-minute limit)
- "what's the weather"

Expected: notifications such as "No action taken." or "That value is outside the allowed
range." Never an action.

**MT-25 — Background speech while asleep.** Put it to sleep ("sleep ev"), then talk
normally or play a podcast for a minute, including words like "next", "pause" and "close".
Expected: the indicator stays **Asleep** and nothing happens. Awake, the same podcast could
trigger commands, which is why you put it to sleep when you're done.

**MT-26 — The indicator never takes focus.** Wake it, type in Notepad, then say
"minimize this".
Expected: Notepad, not the indicator, is minimised.

**MT-26b — Click to wake.** While **Asleep**, click into Notepad, then click the
indicator once and say "minimize this".
Expected: the indicator turns green on the click, and Notepad is minimised. Clicking the
indicator never takes focus away from your window.

**MT-26c — Right-click and drag.** Right-click the indicator.
Expected: the same menu as the tray dot. Then drag the indicator somewhere else, quit, and
start again: it comes back where you left it.

## 5. Kill switch

**MT-27 — Mute.** Right-click the indicator (or the tray dot) → **Mute**. Say "Hey
Jarvis, next tab".
Expected:
- the indicator shows grey **Muted**, and nothing happens;
- clicking the indicator once (or the menu's **Listen**) makes it listen again;
- a double-click only unmutes; it doesn't also start a command.

Windows' own microphone icon stays on while muted. That is expected: audio is dropped as it
arrives, but the stream stays open.

**MT-28 — Emergency-stop hotkey.** Press **Ctrl+Alt+Backspace**.
Expected: **Muted**, and the tray tooltip says "Emergency stop — muted. Unmute to listen
again." Right-click the tray: the menu shows **Emergency stop (ctrl+alt+backspace)**. Unmute with
**Listen**.

**MT-29 — Stop a command mid-way.** On a long video say "skip forward 10 minutes", and
press Ctrl+Alt+Backspace immediately.
Expected: the seek stops early, so the video moves less than 10 minutes. The window is
2–3 seconds, so retry if you're late.

**MT-30 — Stop during a question.** Say "close this window", and while the dialog is open
press Ctrl+Alt+Backspace.
Expected: the dialog disappears and nothing closes.

**MT-31 — Mute is remembered.** Mute, quit from the tray, start again.
Expected: it starts **Muted**, and the tooltip says it is open but nothing is heard. Click
**Listen** to resume.

## 6. Cloud (Groq)

Without a key the agent still works fully for listed commands. These tests add the cloud.

**MT-32 — Add the key.** Tray → **Settings...** → paste the key → **Save**.
Expected:
- "Key saved in Windows Credential Manager".
- The key field empties. The key never stays visible and is never written to a file.

Restart the app for the cloud to take effect.

**MT-33 — Rephrased commands.** Say:
- "go to the tab on the right"
- "make it quieter"
- "jump ahead thirty seconds"

Expected: they work like next tab, volume down and seek forward 30 s. There may be a short
extra delay (a network round trip).

**MT-34 — Ambiguous stays safe.** Say "close it" and "do that again".
Expected: nothing happens, or for "close it" the Yes/No dialog. Never an unconfirmed close.

**MT-35 — Offline fallback.** Turn Wi-Fi off. Say "go to the tab on the right", then
"next tab".
Expected:
- The first gives, after a few seconds, "The online intent model is unavailable; local
  commands still work."
- "next tab" works normally.

Turn Wi-Fi back on.

**MT-36 — Cloud switch off.** Settings → untick "Ask Groq when a phrase is not a known
command" → Save → restart. Say "go to the tab on the right".
Expected: "No action taken." No network is used. Tick the box again afterwards.

## 7. Settings and configuration

**MT-37 — Settings persist.** Settings → untick "Listen for a wake word" → Save → quit →
start.
Expected: still unticked. The file in the [settings table](#0-before-you-start) contains
`enabled: false` under `wakeword`. With the wake word off, every sentence you say is
treated as a command, so test it somewhere quiet. Re-tick afterwards.

**MT-38 — Choose a microphone.** Settings → pick a specific microphone → Save → restart.
Expected: the tray tooltip names that microphone.

**MT-39 — Optional plugin.** Quit. In your settings file add:

```yaml
plugins:
  enabled: [browser, media, window-management, zoom]
```

Start, open a web page, and say "zoom in", "zoom out" and "reset zoom".
Expected: the page zooms. Remove `zoom` (or the whole block) afterwards.

**MT-40 — Bad plugin name.** Use `enabled: [browser, media, window-management, nope]`
and start.
Expected: a notification "Plugin 'nope' was not loaded: not found", and the other commands
still work. Undo the change.

**MT-41 — Broken settings file.** Quit. In your settings file put
`wakeword: {threshold: 5}` and start.
Expected: a "Configuration error" naming `wakeword.threshold` (a dialog for the packaged
app, a terminal message from source), and the app doesn't start. Delete the line and start again normally.

## 8. Robustness

**MT-42 — Unplug the microphone** (USB or Bluetooth) while it's listening.
Expected: within about 2 seconds the indicator turns red, **Microphone offline**. Plug it
back in: green **Listening** again within a few seconds, with no restart.

**MT-43 — Long session.** Leave it running for an hour or more while you work normally,
issuing a command now and then.
Expected: commands still work, nothing fires on its own, and memory use in Task Manager
stays roughly steady.

**MT-44 — Quit releases the microphone.** Tray → **Quit**.
Expected: the tray dot and indicator disappear, and Windows' microphone-in-use icon goes
away within a few seconds.

## 9. Privacy checks

Open `%LOCALAPPDATA%\voice-pc-agent` in Explorer (tray → **Open logs** opens `logs`).

**MT-45 — No audio on disk.** Search the folder, and your profile if you like, for
`.wav`, `.mp3`, `.flac` or `.ogg` files created during testing.
Expected: none created by the agent.

**MT-46 — Logs hold no speech.** Open `logs\voice-agent.log`.
Expected:
- no sentence you spoke;
- no API key (anything key-shaped is masked).

**MT-47 — Audit log.** Open `audit\decisions.jsonl`.
Expected:
- one line per command, with intent, decision, reason and latency;
- what you said appears only as a long `transcript_hash`, never as words.

**MT-48 — Diagnostics.** Tray → **Copy diagnostics**, then paste into Notepad.
Expected:
- versions, settings, devices, plugins, timings and recent decisions;
- `Groq API key: configured` (or `not configured`), but never the key itself;
- no transcripts.

**MT-49 — Clear command history.** Tray → **Clear command history**.
Expected: "Command history cleared. The audit log and log files are kept." History is only
recorded if you turned on `privacy.store_transcripts`, which is off by default.

## 10. Console and dry-run modes (from source)

**MT-50 — Dry run.** Run `.venv\Scripts\voice-agent --dry-run`, type `next tab`,
`skip forward 30 seconds`, `close this window` (answer `n`) and `delete all my files`, then
`quit`.
Expected: each shows resolved / policy / simulated / result, and **nothing is pressed** on
your desktop. The close asks for y/N, and the delete resolves to nothing.

**MT-51 — Console live mode.** Run `.venv\Scripts\voice-agent`.
Expected:
- a warning that the emergency stop is `ctrl+alt+backspace`;
- commands work by voice, with results printed;
- "close this window" asks `[y/N]` in the console.

Press Ctrl+Alt+Backspace: "Muted. Press U to listen again." Press **U**: "Listening again."
Ctrl+C quits.

**MT-52 — Typed keys can't undo the stop.** In console mode, type `quit`, without pressing
Enter, while it's listening. Then press Ctrl+Alt+Backspace.
Expected: it stays muted. Only a **U** pressed after the stop unmutes.

## 11. Optional measurements

**MT-53 — Cloud latency** (needs the key). From the project folder:

```powershell
.venv\Scripts\python scripts\check_groq_connection.py --intent "go to the tab on the right" --repeat 20
```

Record the p50 and p95 in [ADR 0002](adr/0002-groq-model-id.md).

**MT-54 — Cloud accuracy on the test phrases** (needs the key; about 6 minutes, paced to
the rate limit):

```powershell
.venv\Scripts\python scripts\score_corpus.py --cloud --show-misses
```

Expected: the `false acc` column is 0% in the ambiguous, out_of_scope, adversarial and
background rows. Note the paraphrase accuracy.

**MT-55 — Speech-to-text speed.** If you have a 16 kHz mono WAV of yourself saying a
command:

```powershell
.venv\Scripts\python scripts\benchmark_stt.py --wav command.wav
```

Expected: `local path p95` within 400 ms is the goal on this PC.

## Reporting a problem

For each failure, note:
1. the test ID, what you did, what you expected, and what happened;
2. **Copy diagnostics** from the tray, pasted in (it contains no key and nothing you said);
3. the end of `%LOCALAPPDATA%\voice-pc-agent\logs\voice-agent.log` around the time it
   happened.

Most symptoms have a fix in [troubleshooting.md](troubleshooting.md). The full command list
is in [command-reference.md](command-reference.md).

## Results

| Section | Tests | Pass | Fail | Skip |
|---|---|---|---|---|
| 1 Install | MT-01–02 | | | |
| 2 First run | MT-03–05 | | | |
| 3 Commands | MT-06–21 | | | |
| 4 Safety | MT-22–26 | | | |
| 5 Kill switch | MT-27–31 | | | |
| 6 Cloud | MT-32–36 | | | |
| 7 Settings | MT-37–41 | | | |
| 8 Robustness | MT-42–44 | | | |
| 9 Privacy | MT-45–49 | | | |
| 10 Console | MT-50–52 | | | |
| 11 Measurements | MT-53–55 | | | |
