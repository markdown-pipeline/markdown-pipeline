# Troubleshooting: install

## Install and open

On Linux, one command installs everything under your home folder (no administrator rights, nothing system-wide):

```bash
curl -fsSL https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.sh | sh
mdpipeline
```

It installs the latest public release. No GitHub account, token or sign-in is needed.

`mdpipeline` opens the app in your browser. The first time it makes a folder `~/markdown-pipeline` and shows the first screen.

## Problems

- **`mdpipeline: command not found`.** Run `uv tool update-shell`, then open a new terminal. If you installed with a plain Python venv, run
  `~/.mdpipeline-venv/bin/mdpipeline`, or put `~/.mdpipeline-venv/bin` on your PATH.
- **"No release was found" or "Could not look up the latest release".** The installer reads the public release list on GitHub. Check your network
  (a proxy or firewall may block github.com) and run the command again. If it says no release was found, none is published yet.
- **Neither `uv` nor `curl` was found.** Install `curl` (on Debian or Ubuntu: `sudo apt install curl`) or install
  [uv](https://docs.astral.sh/uv/) yourself, then run the installer again. It is safe to run again any time.
- **"Python is too old" or "No module named venv".** The installer brings its own Python 3.12, so this happens only with a manual
  `pip install`. Use the install command at the top of this page. For a plain venv you need Python 3.12 or newer
  (on Debian or Ubuntu: `sudo apt install python3-venv`).
- **Port 8765 is busy.** Run `mdpipeline start --port 0` to use a free one.
- **The window did not open.** The address is printed in the terminal (`http://127.0.0.1:<port>`). Open it yourself.
- **Background sync is on but not running.** Run `mdpipeline background`, or press "Start it" on the Pipelines screen.
- **macOS and Windows.** Linux is the tested system for this release. macOS should work with the same command but is untested. On Windows use
  WSL 2 (Ubuntu): `wsl --install -d Ubuntu`, then run the Linux command inside Ubuntu.

## Update and uninstall

- `mdpipeline update` moves the program to the newest release (it runs the install command again). Running the install command yourself does the same. Your settings and files are not touched.
- `mdpipeline uninstall` turns background sync off, removes its login item and prints the one command that removes the program. Your synced
  Markdown files stay where they are. The app's own data (settings, saved tokens) is in the folder it names; delete that too if you want
  everything gone.
- **If you run it with `uvx`** (or added it from the MCP Registry): the first sync turns background sync on, so uvx has left a login item
  (and a user service where systemd is available) behind. To turn it off and remove the program, run `uvx mdpipeline uninstall`, then
  `uv cache clean mdpipeline`. `uv cache clean mdpipeline` on its own does not stop background sync: the background process and its
  login item keep working from their own copy of the program. To only turn background sync off, run `uvx mdpipeline schedule background off`.
