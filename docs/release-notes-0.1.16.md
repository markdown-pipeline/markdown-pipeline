# Markdown Pipeline 0.1.16

**Markdown Pipeline 0.1.16.** Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.

## Changes

- `mdpipeline update` now restarts background sync on the new version, after the current sync finishes. `mdpipeline schedule` and Settings show which version background sync runs.
- The MCP server now tells your AI agent which version it runs. Before, the version was empty.

## If you're updating from 0.1.15

- The update still prints the old advice ("off, then on"), because 0.1.15 runs the update. You can ignore it: the next `mdpipeline` command you run restarts background sync on 0.1.16. Or run `mdpipeline schedule background restart`. From 0.1.16 on, `update` does this for you.

## Usage data (only if you chose "Share usage")

- No new events.
