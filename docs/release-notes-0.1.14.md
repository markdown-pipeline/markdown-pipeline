# Markdown Pipeline 0.1.14

Linux only for now. Update with `mdpipeline update`; nothing else to do. Windows and macOS are not supported in this release.

## Fixes

- **Fixes a blank first screen that could appear on slow starts.** The app's page could ask for the first-run screen before all of its parts had loaded. Now it starts only when everything has loaded.
- **The "Source folder not found" notice goes as soon as you choose a new folder that exists.** Before, it stayed (and showed your new folder as "not found") until the next sync.

## Also in this release

- This is the first version published on PyPI, so `uvx mdpipeline` and the MCP Registry can use it. Versions 0.1.11, 0.1.12 and 0.1.13 were skipped on PyPI: 0.1.11 is on GitHub Releases only, and 0.1.12 and 0.1.13 were never released.

## Usage data (only if you chose "Share usage")

- No new events.
