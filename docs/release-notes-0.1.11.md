# Markdown Pipeline 0.1.11

Linux only for now. Update with `mdpipeline update`; nothing else to do. Windows and macOS are not supported in this release.

## Fixes

- **A GitHub sign-in that GitHub rejects is renewed once and the sync retried, before any error.** Before, a pipeline that writes to a GitHub repo with "Sign in with GitHub" could fail with "create a new fine-grained token", advice that does not apply to a sign-in. Now the sign-in is renewed and the same request is tried again. If GitHub still refuses, the message is: "GitHub did not accept your sign-in even after it was renewed. Sign in again (nothing was written or deleted)."

## New

- **Your agent can start the MCP server before anything is set up.** `mdpipeline mcp` used to stop with an error when no output folder existed yet. Now it starts, and each tool answers with one next step: run `mdpipeline start` (or `uvx mdpipeline start` if you use `uvx`). When the first sync is done, ask again; no restart is needed.
- **`uvx` and the MCP Registry are recognised as ways to install.** The commands shown to you (the next step above, and `usage on|off`) use the form that works for how you installed: `uvx mdpipeline ...` for `uvx` and Registry users, `mdpipeline ...` for everyone else. `uv cache clean mdpipeline` updates a `uvx` install and leaves background sync working.

## Usage data (only if you chose "Share usage")

- Starting the server before setup is counted once per server run, as the existing `notice_shown` event with the kind `setup_mode`. Installs are reported with the install method `uvx` or `registry`. See `what-we-send.md`.
