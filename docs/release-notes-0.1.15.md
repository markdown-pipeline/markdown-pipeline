# Markdown Pipeline 0.1.15

**Markdown Pipeline 0.1.15.** Linux only for now: Windows and macOS are not supported in this release. Update with `mdpipeline update`. Nothing else to do.

## Fixes

- **A saved sign-in or token can no longer be deleted while another output folder still uses it.** All your output folders share one vault. If you keep more than one (for example, a test folder and a real one), a pipeline in one folder can use a secret saved from another. Before, `mdpipeline vault list` showed "used by: -" for that secret, and `vault remove` deleted it. The pipeline's next sync then failed until you signed in again. Now:
    - `vault list` names the pipeline and its output folder, for example `b-gh (in brain "brainB")`. On screen, "brain" means an output folder.
    - `vault remove` refuses, says who still uses the secret, and says "Nothing was removed."
    - The same check protects the secret when you sign out of a pipeline, remove its token, or remove the pipeline, in the terminal and in the app.
    - If another output folder's configuration can't be read, nothing is removed. The message names that folder so you can fix it first.

## Known limit

- The check only sees output folders that have synced at least once, plus the one you name in the command. A secret used only by a folder that has never synced can still be removed. To protect it, sync that folder once.

## Usage data (only if you chose "Share usage")

- No new events.
