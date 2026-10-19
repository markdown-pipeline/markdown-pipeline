# What we send

Markdown Pipeline can send anonymous usage data so we can see where setup is hard. **It is off until you choose.** If you choose "Don't share",
or never answer, nothing is sent. You can change your mind any time in **Settings → Privacy & usage**, or with `mdpipeline usage on` and
`mdpipeline usage off`.

Your files, and what is in them, never leave your computer through this. Markdown Pipeline only reads your sources (it never changes them) and
writes Markdown files to a folder on your computer, or to a GitHub repo you choose. The folder browser (the **Browse** button) lists only folder names on your computer, to your
screen; nothing it shows is sent anywhere.

## What is never sent

Titles, file or folder names, paths, search or JQL text, project keys, URLs, site names, repository names, email addresses (except the one you
type yourself in a source request, below), tokens and error messages. There is no free-text field on any automatic event.

## Automatic events (only if you chose "Share usage")

Each event is a name plus a few fixed choices, counts or durations. Counts are grouped (for example "1-10", "11-100"). Every event also carries a
random install ID (you can replace it any time with `mdpipeline usage reset-id`), the app version, and a random number that changes each time
you open the app. We do not build a profile of a person.

| When | Event | What it carries |
|---|---|---|
| You open the app | `app_opened` | whether it is the first time |
| You open a new version | `app_updated` | the previous version |
| You install | `install_completed` | your operating system and how you installed |
| You answer the usage question | `consent_decided` | that you chose "Share" |
| You move through the first run | `onboarding_step_viewed`, `onboarding_step_completed`, `session_ended_before_first_sync`, `vault_passphrase_needed` | which step, and how long it took; that this computer has no system keychain (nothing you typed) |
| You start adding a source | `source_add_started` | which kind of source (Jira, folder...), and where you started |
| You sign in to a source | `source_auth_started`, `source_auth_completed`, `source_auth_failed` | the kind of source, token or not, and a short error class such as "wrong credentials" |
| A source is connected | `source_connected`, `source_added`, `source_toggled`, `source_removed`, `pipeline_created` | the kind of source, how many sources or pipelines, on or off, and how old it was (a range) |
| You choose where files go | `target_connected` | folder or GitHub |
| You narrow what is read | `source_scope_edited` | the kind of filter and which fields it uses (such as "status"), never the values |
| You choose a folder | `folder_pick` | whether you browsed, typed the path or took a recent folder. Never the path or the folder's name |
| You choose Jira projects | `jira_project_pick` | whether you ticked projects in the list or wrote your own query, and how many (a range). Never the project names or keys |
| You choose a GitHub branch | `branch_pick` | whether you chose it from the repository's list or typed it. Never the branch name |
| You preview the first sync | `dry_run_viewed` | how many files, as a range |
| Your first files are written | `first_sync_completed` | the kind of source, how many files (range) and how long it took |
| A sync runs | `sync_completed`, `sync_failed` | what started it (you or the schedule), counts as ranges, duration, and an error class |
| A sync waits for your OK | `guard_triggered`, `guard_resolved` | that it happened, and whether you approved or cancelled |
| A notice appears | `notice_shown` | which kind (failed, stale...) |
| You change the sync interval | `schedule_changed` | the old and the new interval |
| You open Activity or the pipeline view | `activity_viewed`, `view_mode_opened` | where you came from |
| You open a help page or see an error | `help_opened`, `error_shown` | which topic, which area and error class |
| You connect or copy for your agent | `agent_connected`, `agent_snippet_copied` | which agent (Claude Code or Codex) and whether it was one click or a copy |
| Your agent reads the files | `agent_read`, `first_agent_read` | how many reads that day, and how long after the first sync. Nothing about what was read |
| You try the sample | `try_started`, `try_completed` | that you started it, and how long it took |

Every event also says, as a plain yes or no, whether the install belongs to the developers (`is_internal`), so our own testing is not counted as users.
It is `no` for you and says nothing about you.

The exact list, with every field and allowed value, is in [usage-events.md](usage-events.md). That file is generated from the allowlist in the
code and a test fails if the two differ. Anything not on that list is dropped before it is stored.

## Things you send on purpose

Two screens let you send words, only when you press **Send**. These go out even if sharing is off, because you pressed the button.

- **Request a source** (`source_requested`): the source you picked from the list, or the name you typed (up to 100 characters), and an email
  address only if you choose to add one so we can tell you when it is ready.
- **One-question feedback** (`feedback_answered`): which question, the answer you picked, and a comment only if you write one (up to 500 characters).

## How it is sent, and how to see it

Events wait in a small file on your computer and are sent in batches over https to PostHog's US cloud (`us.i.posthog.com`), and only there. A
failed batch stays queued. The app never waits for the network.

- `mdpipeline usage log` (or Settings → Privacy & usage) shows the last 50 events sent, exactly as sent.
- `mdpipeline usage off` stops it at once and drops anything waiting.

Apart from this, the app makes network calls only to the services you connect: your Atlassian site (we only read; this includes one call that lists the
projects your account can read, so you can tick them instead of typing keys; the names stay on your computer), and `api.github.com` (plus the two
GitHub sign-in addresses) if you use a GitHub repository as the destination. Everything is https. The app's window listens on your own
computer only (`127.0.0.1`).
