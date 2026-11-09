# Markdown Pipeline 0.1.20

**Markdown Pipeline 0.1.20.** Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.

## Changes

- **A note when a newer version is out.** At most once a day, the app checks PyPI, the public list of Python packages, for a newer Markdown Pipeline. If there is one, you see one line after a command (for example `Markdown Pipeline 0.1.21 is out (you have 0.1.20). Update: mdpipeline update`) and a notice in the app with **Copy**, **What's new** and **Hide**. The check sends no ID and nothing about you or your files, and it is not tied to the usage-data choice. To turn it off: `mdpipeline update-check off`, or set `MDPIPELINE_NO_UPDATE_CHECK=1`. Turn it back on with `mdpipeline update-check on`.
- **Windows groundwork (Windows is not supported yet): Claude Code and Codex installed with npm now connect.** The app starts them by the full path it finds, so `claude.cmd` and `codex.cmd` work. If the folder's path has a character that Windows cannot pass to them safely (such as `&`, `%`, `!` or a quote), the app says which characters and asks you to move or rename the folder, instead of running anything.
- **`mdpipeline update` run outside your output folder no longer prints a false "Error: No output folder is set up" line.** The update itself always worked; the line only made it look as if it had not. The lines also come out in order now: "Running: …", the tool's output, then "Updated to version …".
- **New: `mdpipeline --version` (or `-V`)** prints the version, from any folder.
- **Background sync status no longer contradicts itself.** While background sync restarts, `mdpipeline schedule` says it is restarting, not "not running". When it really is not running, it gives one way to start it again: `mdpipeline schedule background restart` (before, it also suggested `mdpipeline background`, which only runs in that terminal). The app's "did not start" message says the same.
- **Clearer tools for your AI agent (MCP).** Every tool and every parameter now says what it does, whether it only reads or also writes, what it returns and in what order, what happens on a bad input, and when to use it instead of a sibling tool. No tool changed its name or behavior.

## Usage data (only if you chose "Share usage")

- **New counts:** `update_notice_shown` (the newer-version note was shown; says which version you have, which is out, and whether in the command line or the app) and `update_notice_acted` (the first time you run the version a note named; says the two versions). No file names, no paths and no content. Same consent as before: if you did not choose "Share usage", nothing is sent.
- The notes themselves, and the daily check for a newer version, work either way.
