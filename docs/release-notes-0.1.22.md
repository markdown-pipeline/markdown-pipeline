# Markdown Pipeline 0.1.22

**Markdown Pipeline 0.1.22.** Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.

## Changes

- Fixed: stepping through the schedule list with the arrow keys no longer jumps back to the old value or loses focus when the page redraws.
- Fixed: on Linux, if a file can't be written, deleted, or the schedule can't be saved because of a folder permission, the message now says it's a permission problem and names the folder. For the schedule it also says your previous schedule is unchanged.
- Usage data (only if you chose to share it): we can now tell your first sync of your own source apart from the built-in sample. This adds a yes/no flag to some events and one extra event with the same details as the first-sync event (kind of source, file-count range, time taken). Nothing about what's in your files or their names. See "What we send".
