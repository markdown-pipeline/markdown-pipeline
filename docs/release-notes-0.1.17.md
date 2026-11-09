# Markdown Pipeline 0.1.17

**Markdown Pipeline 0.1.17.** Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.

## Changes

- A file with a front matter mistake (front matter is the block between the --- lines at the top) no longer makes the whole sync fail. That file is skipped and not changed, your other files sync, and the run is `ok` with a warning. The warning names the file, says near which line the mistake is when it can, and how to fix it or exclude the file. You see it in the command output and on the app's home screen.

## Behaviour change

- For scripts: when a file has a front matter mistake, the run now ends `ok` (exit code 0) with a warning, not with exit code 1. Scripts that relied on exit code 1 for this case will no longer see a failure.

## Usage data (only if you chose "Share usage")

- **One new count, `front_matter_warning`:** how many files a sync skipped because of a front matter mistake. It has no file names, no paths and no line numbers. Same consent as before: if you did not choose "Share usage", nothing is sent. The warning itself is shown on your machine and saved in your local log either way.
- **If you install or update with the install script,** `install_completed` and `app_updated` now also say which release file the script downloaded from our GitHub releases: the version and the file name of our wheel. On a first install, this waits for your answer to the usage question, and it is deleted if you choose "Don't share".
- **`app_updated` now also says how you installed**, the same way `install_completed` does. Installs and updates that don't use the install script never send a release file.
