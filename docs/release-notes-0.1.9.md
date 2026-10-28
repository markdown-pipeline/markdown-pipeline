# Markdown Pipeline 0.1.9

A fix release. Update with `mdpipeline update`; nothing else to do.

## Fixes

- **Scheduled sync after a clock jump.** If your computer's clock ran ahead and was then corrected (it happens right after boot on some Linux
  machines, for example dual-boot setups that keep the hardware clock in local time), the next scheduled sync used to wait for the clock to
  catch up, up to several hours. It now runs on the next check. `mdpipeline status` no longer shows a negative age.
- **Background sync waits for a missing folder.** If your output folder is not there at a check (for example an external or network
  drive that is not mounted yet), that check is skipped without an error, and syncing continues by itself as soon as the folder is back.
  It is forgotten only after the folder has been missing for 7 days in a row (temporary folders right away).
- **An older background-sync login item is brought up to date** when you update or when the background process starts. If you never turned
  background sync on, or you turned it off, nothing is created or changed.

## Usage data (only if you chose "Share usage")

- One new anonymous event, `feedback_shown`: which one-question card was shown (one of a fixed list). Each card appears only once on a computer, so it is sent once per card. Never the
  question's text or your answer. It is listed in `what-we-send.md` and `usage-events.md` in this repository.

## Behind the scenes

- The test suite no longer leaves background processes behind or touches the real user folders, and `uv.lock` is committed with each version.
