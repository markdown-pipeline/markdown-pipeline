# Troubleshooting: connect your agent

Markdown Pipeline connects Claude Code or Codex to a **read-only** local server that lets the agent search and read your synced files. You
connect with one click on the last screen of the first run (or Settings → Agents). The app shows the exact command first, runs your agent's
own `mcp add` command, and writes a short `AGENTS.md` into the synced folder. It never edits an agent's settings file by itself.

## Problems

- **"Claude Code isn't installed on this computer."** The app looks for the `claude` (or `codex`) command on your PATH. Install the agent, or
  if it is installed, open a new terminal so the PATH is up to date, then press the button again. You can always connect by hand: use the
  "Another agent" section for the command to copy, or run `mdpipeline connect`.
- **The agent command failed.** The app shows the agent's own message. Fix what it says (often a sign-in or an update of the agent), then
  press the button again. Pressing it twice is safe: the second press refreshes the registration.
- **You already have an `AGENTS.md` in that folder.** The app never overwrites it without asking. Choose "Keep yours, add ours as MARKDOWN-PIPELINE.md", or
  "Replace yours (a copy is kept)" (yours is saved as `AGENTS.md.before-markdown-pipeline`).
- **The agent is connected but does not see the files.** Restart the agent (a new `claude` or `codex` session) so it loads the server. Then ask
  the suggested question. The first screen after connecting shows how many reads the agent has made.
- **Disconnect.** Settings → Agents → Disconnect removes the registration and the file we wrote (and puts yours back). Nothing else is touched.

## Connect by hand

```bash
mdpipeline connect          # prints the command for Claude Code and the config for Codex
```
