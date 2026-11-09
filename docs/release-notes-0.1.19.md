# Markdown Pipeline 0.1.19

**Markdown Pipeline 0.1.19.** Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.

## Changes

- Background sync that stopped is now noticed and fixed. If background sync is on and its start-at-login entry is gone, the app puts it back. If the background process is not running, `status`, `schedule`, the app's Home and Settings screens, and the MCP tools say so (with the time of the last good sync) and offer **Start sync again**. On a machine with no systemd and no desktop session, the app tells you it cannot start at login instead of saying it will.
- Fixed: while a restart of background sync is under way, status now says it is restarting, instead of "Restart it". If it has not come back after 2 minutes, the "Restart it" line shows again.
- Fixed: anything you type on a screen (a token, a passphrase, a folder, your email) is kept, with your cursor, when the screen redraws, on the setup screens and in the pipeline editor.
- Fixed: if one of the app's script files fails to load, the screen still works. Browse buttons are simply missing, or you see a calm "Reload the page" line, instead of a broken screen.

## Usage data (only if you chose "Share usage")

- **New counts:** `background_self_healed` (the start-at-login entry or the process was restored; says which, and whether through the CLI, the app or MCP), `notice_shown` with kind `background_not_running` (once per outage), `notice_acted` (you used **Start sync again** in the app, or ran `mdpipeline schedule background restart` while background sync was not running; says which), and `error_shown` with `script_missing` (once per page load; says which script did not load). No file names, no paths and no content. Same consent as before: if you did not choose "Share usage", nothing is sent.
- The notices themselves are shown on your machine either way.
