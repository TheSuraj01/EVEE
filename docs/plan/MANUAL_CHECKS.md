# Manual checks — things only a human on real Windows hardware can verify

Claude Code cannot test these. Work through them at the end of each phase.

## After Phase 1 (actions)
- [ ] `ui.exit_fullscreen` leaves full screen in a browser, in VLC, and in a game
- [ ] `browser.next_tab` works in Chrome, Edge and Firefox
- [ ] `window.close` prompts before closing, and the prompt can be dismissed
- [ ] Nothing fires into an elevated window (expected) and the failure is explained clearly
- [ ] "skip forward 30 seconds" moves ~30 s in YouTube (arrow = 5 s); note VLC/Netflix drift
- [ ] "volume up" changes Windows volume by ~10% (5 media-key presses)
- [ ] "play"/"pause" work with the browser in the background (global media key)
- [ ] "close this window" with only the desktop focused does nothing (no shutdown dialog)
- [ ] "close this window" on an unsaved Notepad shows Notepad's own save prompt
- [ ] Confirming "close this window" in the console closes the window focused *before* the
      prompt (not the console); if that window was closed meanwhile, nothing closes
- [ ] "minimise this" / "maximise this" act on the window that had focus before speaking

## After Phase 2 (voice)
- [ ] Wake word fires from 2 m away at normal speaking volume
- [ ] Wake word does NOT fire during 10 minutes of TV or podcast audio
- [ ] Unplugging the mic mid-session shows a clear error, and replugging recovers
- [ ] Whisper `base` latency is acceptable on this machine (target < 600 ms for 2 s)
- [ ] Idle CPU with the wake word running is tolerable (target < 8% on an i7)
- [ ] `voice-agent --no-llm` first run downloads `hey_jarvis` + Whisper `base`, then starts offline
- [ ] "hey jarvis, next tab" in one breath switches tab (the command start is not clipped)
- [ ] "hey jarvis" then silence reports "I didn't hear a command" (~8 s) and nothing fires
- [ ] TV/background speech without the wake word never reaches Whisper (no "heard ..." lines)
- [ ] Ctrl+C stops cleanly mid-sentence and while a confirmation prompt is showing
- [ ] Tune `audio.vad_energy_floor` if commands are cut short (too high) or never end (too low)

## After Phase 3 (cloud)
- [ ] Pull the network cable: exact commands still work, with a visible status change
- [ ] A wrong API key gives a clear message, not a silent failure
- [ ] Record the measured round-trip latency in ADR 0002

## After Phase 4 (UI)
- [ ] `voice-agent --tray`: the dot icon appears; hover shows the state; Mute/Listen toggles it
- [ ] "close this window" in tray mode shows a Yes/No dialog that closes itself after 10 s (= No)
- [ ] A spoken command shows a toast with what was heard and what happened
- [ ] The mic indicator is visible on every monitor and in full-screen apps
- [ ] The emergency-stop hotkey works while an action is executing
- [ ] On this dev PC Ctrl+Alt+Space is owned by another program (Win32 error 1409): set
      `app.emergency_stop_hotkey` in config/user.yaml (e.g. `ctrl+alt+pause`) and re-check
- [ ] "skip forward 2 minutes" then the hotkey: the seek stops early (key loop interrupted)
- [ ] Settings survive a restart; the API key lands in Credential Manager, not a file

## After Phase 5 (plugins)
- [ ] Add `zoom` to `plugins.enabled` in config/user.yaml: "zoom in" zooms a browser page
- [ ] Enable a plugin name that doesn't exist: tray mode shows a "not loaded" notification,
      and Copy diagnostics lists it under Plugins

## After Phase 6 (release)
Step-by-step version, with expected results: [docs/manual-testing.md](../manual-testing.md).

- [ ] `python scripts/benchmark_stt.py --wav <a 16 kHz mono recording of "next tab">`:
      local path p95 within 400 ms with Whisper `base` on the target i7 (record the table)
- [ ] `python scripts/score_corpus.py --cloud --show-misses`: paraphrase accuracy, and zero
      false accepts in the ambiguous/out-of-scope/adversarial/background rows
- [x] `scripts\build_windows.ps1`: build, secret scans, clean-profile smoke test and version
      stamp all pass (2026-10-20, PyInstaller 6.22.3)
- [ ] Mute in the tray, then speak: nothing happens, and unmuting resumes; the Windows mic icon
      stays on while muted (documented)
- [ ] Packaged app first run (`dist\VoicePCAgent\VoicePCAgent.exe`, internet on): models
      download, the indicator goes green, "Hey Jarvis, next tab" works
- [ ] Start the exe a second time: "already running" dialog, no second tray dot
- [ ] On this PC Ctrl+Alt+Space is taken: a notification says the emergency stop is
      Ctrl+Alt+Backspace, and that key mutes the agent
- [ ] Settings → untick "Listen for a wake word", Save, quit, restart: the change is kept
      (`%LOCALAPPDATA%\voice-pc-agent\user.yaml` for the exe)
- [ ] Console app (`voice-agent`): emergency stop, then **U** listens again
- [ ] Record a short demo GIF (wake word → next tab → indicator) for the README
- [ ] First release: push a `v0.1.0` tag to a fork; the release has the zip, SHA256SUMS.txt
      and the privacy notes, and `Get-FileHash` matches
- [ ] security.yml's `detect-secrets scan > /dev/null` step can never fail; decide whether to
      gate on a baseline (`detect-secrets audit`) or drop it
- [ ] The build runs on a clean Windows VM with no Python installed
- [ ] `grep` the build output for `gsk_` — must be empty
- [ ] First-run model download works, and the app then starts offline
