# Markdown Pipeline

Your team's tools as clean Markdown files, kept current for your AI agents. Early access: Jira Cloud and folders on your computer, on Linux.

- **Local.** It runs on your computer. Your files go to a folder you choose, or to a GitHub repo you choose.
- **We only read.** We never change anything in Jira or in your source folders.
- **Raw layer only.** It does not summarize or analyze anything. Your agent (Claude Code, Codex) does that, from the files.
- **It sees what you see.** It reads Jira with your own account's API token, so the files hold only the issues your account can read. The token never appears in the files, the logs or your agent's answers. Anyone who can open the folder or repo you choose can read the files, so choose it the way you'd choose who sees those issues.
- **Open source.** Apache-2.0. The code that reads your Jira is in this repository.

## Install

Linux (tested). One command. It installs `uv` if you don't have it (a Python tool manager), then the app, all under your home folder, with no administrator rights:

```bash
curl -fsSL https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.sh | sh
```

It installs the latest release from this page. No GitHub account, token or sign-in is needed. Then type `mdpipeline`: your browser opens the app. If your shell says `mdpipeline: command not found`, open a new terminal (or run `uv tool update-shell`) and try again.
Run the same command again any time to move to the newest release; your settings and pipelines are kept.

**Platforms:** Linux (tested). macOS is untested. On Windows, use WSL 2 (Ubuntu) and run the command inside it. On a server or in WSL 2, background sync stops when you log out unless you run `loginctl enable-linger $USER` once; after a reboot, logging in or running any `mdpipeline` command starts it again.

## Use it from your agent (MCP)

The app includes a local MCP server, so Claude Code or Codex can search and read the files.

```bash
mdpipeline connect --agent claude   # or: --agent codex
```

It prints the exact command (Claude Code) or config block (Codex) for your setup. Your agent starts the server itself when it needs it; nothing extra runs in the background. Tools: `search`, `read`, `list`, `recent_changes` ("what changed since yesterday?"), `sync_status` and `sync`. No tool writes or edits your files. The server reads only the Markdown files the app wrote (and your `raw/` and `wiki/` folders), on your computer.

## What we send

Usage data only if you choose to share it, and the words you type in a feedback or source request when you press Send.
The full list, in plain words: [what-we-send](what-we-send.md). Turn it off any time: `mdpipeline usage off`.

## Feedback and problems

Open an issue on this repository: [Issues](https://github.com/markdown-pipeline/markdown-pipeline/issues). Tell us what you tried, what you expected and what happened.

## Uninstall

`mdpipeline uninstall` turns background sync off and prints the one command that removes the program. The Markdown files it wrote stay where they are.

Full reference (commands, settings, safety rules): [docs/reference.md](docs/reference.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
