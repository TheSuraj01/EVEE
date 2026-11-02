# Command reference

Every command the agent can run (57 by default, plus "open <app>" for the apps in your
Start menu), the phrases it recognises without the cloud, and what each does on your PC. Phrases are matched after punctuation, capitals,
filler words ("um", "please", "okay") and little words ("the", "a", "this", "it") are
removed, so "close tab" and "close this tab" are the same phrase. Close mishearings, such as "close the
tap" for "close the tab", still match.

Anything not listed here is either sent to the cloud model, which can only pick one of
these commands (see [privacy.md](privacy.md)), or ignored.

## Talking to it

| You | It |
|---|---|
| Say **"wake up ev"**, or click the indicator | Wakes up: from now on **every sentence is a command** |
| Say **"wake up ev, open chrome"** in one go | Wakes up and runs the command straight away |
| Say **"sleep ev"**, or click the indicator again | Goes to sleep: it only listens for "wake up ev" |
| Press the emergency-stop key, or **Mute** in the menu | Stops listening completely, until you click the indicator |

Change the two phrases in Settings. To say "Hey Jarvis" before each command instead, pick
`hey_jarvis` as the wake word in Settings.

**Risk** decides what happens before a command runs:
- **low**: runs at once.
- **medium**: runs at once. The agent never asks first, only refuses when a check fails.
- **high**: asks you **Yes/No** first. No answer within 10 seconds counts as No.

All shortcuts go to the window that has focus, so click the window you mean first.

## Built in

| Command | Say | Does | Risk |
|---|---|---|---|
| `ui.exit_fullscreen` | exit full screen · leave full screen · get me out of full screen · get out of full screen · close full screen · escape · press escape | Esc | low |
| `ui.enter_fullscreen` | full screen · go full screen · make it full screen · enter full screen | F11 | low |
| `system.sleep` | put the pc to sleep · put the computer to sleep · sleep the pc · sleep the computer · sleep pc · sleep computer | Asks Yes/No, then sleeps the PC (on Modern Standby laptops: Win+X, U, S, the way Start → Power → Sleep does) | high |
| `web.open` | *(cloud only)* "open YouTube in a new tab", "go to github" | Opens the site's https address in a new tab of the browser in front (Chrome, Edge, Firefox...), else your default browser | low |
| `app.open` | open *app* · launch *app* · start *app* · run *app* · open the *app* app | Opens that app from your Start menu | low |

**Apps** are the ones in your Start menu, read when the agent starts (uninstallers are
left out). Say the name as Start shows it ("open Google Chrome") or the everyday name:
"chrome", "edge", "teams" (without the maker's name), "vs code" or "vscode" (initials),
"notepad plus plus". Any other way of naming one goes to the cloud model; the name it
answers with is matched against that same list on your PC, and nothing else is opened. An app installed while the agent runs needs a restart.

**Websites** need the cloud model, which turns the name you say into an address. The agent
opens only plain public `https://` addresses: never a file, a script, a local-network
address (router, printer), a login part or a port. Without a Groq key, "open youtube"
does nothing.

## Browser (`browser` plugin)

| Command | Say | Does | Risk |
|---|---|---|---|
| `browser.next_tab` | next tab · switch to next tab · go to the next tab · move to the next tab | Ctrl+Tab | low |
| `browser.previous_tab` | previous tab · last tab · go back a tab · go to the previous tab · switch to the previous tab | Ctrl+Shift+Tab | low |
| `browser.close_tab` | close this tab · close the tab · close current tab | Ctrl+W | medium |
| `browser.reopen_tab` | reopen tab · undo close tab · bring that tab back · reopen the closed tab · reopen the last tab | Ctrl+Shift+T | low |
| `browser.new_tab` | new tab · open a new tab | Ctrl+T | low |
| `browser.new_window` | open a new window · new browser window | Ctrl+N | low |
| `browser.refresh` | refresh · reload · refresh the page · reload the page | F5 | low |
| `browser.back` | go back · previous page · back page · go to the previous page | Alt+Left | low |
| `browser.forward` | go forward · next page · forward page · go to the next page | Alt+Right | low |
| `browser.find` | find on page · search this page · find | Ctrl+F | low |
| `browser.bookmark` | bookmark this page · bookmark this · add a bookmark | Ctrl+D | low |

## Media (`media` plugin)

| Command | Say | Does | Risk |
|---|---|---|---|
| `media.play_pause` | play · pause · resume · play pause · pause the video · play the video · resume the video · pause the music · play the music · resume playing | Play/Pause key | low |
| `media.seek_forward` | seek forward · skip forward · fast forward · skip ahead · jump ahead · jump forward | Right arrow, once per 5 seconds | low |
| `media.seek_backward` | seek back · seek backward · skip back · rewind · skip backward · jump back | Left arrow, once per 5 seconds | low |
| `media.volume_up` | volume up · louder · turn it up · turn up the volume · turn the volume up · increase the volume · raise the volume · make it louder | Volume Up key, 5 times | low |
| `media.volume_down` | volume down · quieter · turn it down · turn down the volume · turn the volume down · decrease the volume · lower the volume · reduce the volume · make it quieter | Volume Down key, 5 times | low |
| `media.mute` | mute · unmute · silence · mute the sound · unmute the sound · mute the volume · mute audio · unmute audio | Mute key (toggles) | low |
| `media.next_track` | next song · next track · skip song · skip this song · skip track · play the next song | Next Track key | low |
| `media.previous_track` | previous song · previous track · last song · play the previous song | Previous Track key | low |
| `media.stop` | stop the music · stop playback · stop playing | Stop key | low |

Seeking takes a duration: "skip forward 30 seconds", "rewind two minutes". Without one it
seeks 10 seconds. The allowed range is 1 second to 10 minutes; anything outside it is
refused, not shortened. The arrow keys seek 5 seconds a press in YouTube and most web
players, so the duration is rounded up to whole presses: 12 seconds is 3 presses.

Media **mute** is your PC's sound. To stop the agent listening, say "sleep ev" or use
**Mute** in the menu.

## Editing and pages (`editing` plugin)

| Command | Say | Does | Risk |
|---|---|---|---|
| `edit.copy` | copy · copy this · copy that | Ctrl+C | low |
| `edit.paste` | paste · paste it · paste here | Ctrl+V | low |
| `edit.cut` | cut · cut this | Ctrl+X | low |
| `edit.undo` | undo · undo that | Ctrl+Z | low |
| `edit.redo` | redo · redo that | Ctrl+Y | low |
| `edit.select_all` | select all · select everything | Ctrl+A | low |
| `edit.save` | save · save this · save the file | Ctrl+S | low |
| `edit.print` | print · print this · print this page | Ctrl+P (opens the print dialog) | low |
| `page.down` | scroll down · page down | Page Down | low |
| `page.up` | scroll up · page up | Page Up | low |
| `page.top` | go to the top · scroll to the top · top of the page | Ctrl+Home | low |
| `page.bottom` | go to the bottom · scroll to the bottom · bottom of the page | Ctrl+End | low |

## Windows (`window-management` plugin)

| Command | Say | Does | Risk |
|---|---|---|---|
| `window.minimize` | minimize this · minimise this · hide this window · minimize the window · minimise the window | Minimises the focused window | low |
| `window.maximize` | maximize this · maximise this · make this bigger · maximize the window · maximise the window | Maximises the focused window | low |
| `window.focus_next` | next window · switch window · alt tab · switch windows · switch to the next window | Alt+Tab | low |
| `window.snap_left` | snap left · move this left · snap the window left · move the window left | Win+Left | low |
| `window.snap_right` | snap right · move this right · snap the window right · move the window right | Win+Right | low |
| `window.close` | close this window · close the window | Asks the focused window to close, as its X button does | **high** |

Minimise, maximise and close never act on the desktop, the taskbar, a console window or
the agent's own windows. The others are shortcuts, so they act on whichever window has
focus.

## Desktop (`desktop` plugin)

| Command | Say | Does | Risk |
|---|---|---|---|
| `desktop.show` | show desktop · show the desktop · go to desktop | Win+D | low |
| `desktop.minimize_all` | minimize everything · minimise everything · minimize all windows | Win+M | low |
| `desktop.start_menu` | open start menu · start menu · open start | Win | low |
| `desktop.file_explorer` | open file explorer · open my files · open explorer | Win+E | low |
| `desktop.settings` | open windows settings · windows settings | Win+I | low |
| `desktop.screenshot` | take a screenshot · screenshot · snip the screen | Win+Shift+S | low |
| `desktop.task_manager` | open task manager · task manager | Ctrl+Shift+Esc | low |
| `desktop.task_view` | task view · show all windows | Win+Tab | low |
| `desktop.next_desktop` | next desktop | Ctrl+Win+Right | low |
| `desktop.previous_desktop` | previous desktop | Ctrl+Win+Left | low |
| `desktop.notifications` | show notifications · open notifications | Win+N | low |
| `desktop.clipboard` | clipboard history · show clipboard | Win+V | low |

These only press Windows' own shortcuts. Nothing here starts a program by name, so
nothing you say can choose what runs.

## Zoom (`zoom` plugin)

| Command | Say | Does | Risk |
|---|---|---|---|
| `zoom.in` | zoom in · increase zoom · enlarge text | Ctrl+Plus | low |
| `zoom.out` | zoom out · decrease zoom · shrink text | Ctrl+Minus | low |
| `zoom.reset` | reset zoom · normal size · actual size | Ctrl+0 | low |

## Repeats and stopping

- **Repeats:** the same command again within 1.2 seconds is ignored, because one sentence
  can be heard twice. Wait a moment between repeats, or change `nlu.dedupe_window_ms`.
- **Stopping:** the emergency-stop key (default **Ctrl+Alt+Space**, or
  **Ctrl+Alt+Backspace** if another program already uses that) or **Mute** stops a command
  that is still running, such as a long seek, and cancels an open Yes/No question.

## Turning a group off, adding phrases or commands

Each group is a plugin. To turn one off, list only the ones you want in your settings file
(`config\user.yaml`, or `%LOCALAPPDATA%\voice-pc-agent\user.yaml` for the packaged app):

```yaml
plugins:
  enabled: [browser, media, window-management, editing, desktop, zoom]
```

To recognise another phrase for an existing command, add it to the `aliases` in that
plugin's `plugin.yaml`. For the two built-ins, add it in `config/commands.yaml`. New
commands come from plugins: see [plugin-development.md](plugin-development.md).
