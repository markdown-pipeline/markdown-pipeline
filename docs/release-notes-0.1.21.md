# Markdown Pipeline 0.1.21

**Markdown Pipeline 0.1.21.** Linux only for now: Windows and macOS are not supported in this release. The Windows fixes below are groundwork for Windows support. Update with `mdpipeline update`. Nothing else to do.

## Changes

- Fixed: on Windows, the first-run screen now suggests a folder path in the form Windows uses (`C:\Users\you\markdown-pipeline\...`), not a Linux-style `~/...` path.
- Fixed: on Windows, the folder browser's path bar (breadcrumbs) and its Up button now work with drive letters and backslashes.
- Fixed: on Windows, saving the schedule while the app is reading it could fail with "Access is denied". The app now waits a moment and retries, and if it still cannot save, it says so and keeps your previous schedule.
- Fixed: on Windows, a file that is open in another program (Obsidian, an editor) no longer fails the whole sync. That file is skipped with the message "`<name>.md` is open in another program, so it wasn't written. Close it there, and the next sync will try again." (or "…so it wasn't removed…" when the sync would delete it). It is never counted as deleted.
