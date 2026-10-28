# Markdown Pipeline 0.1.10

A fix release. Update with `mdpipeline update`; nothing else to do.

## Fixes

- **An expired or revoked Jira API token no longer looks like an empty Jira.** Jira Cloud answers a search made with a rejected token as if it came from an anonymous visitor: an empty list of issues, not an error. Markdown Pipeline used to take that empty list at face value, so a sync planned to delete every file it had written from Jira. The safety check stopped the plan and asked before deleting anything, but it offered "Approve" and did not say that the token was the cause. Approving would have deleted those files.
  Now Markdown Pipeline confirms the token with Jira before it trusts an empty answer. A rejected token stops the sync with "Jira rejected the email or API token…". The sync changes nothing and offers nothing to approve. Create a new API token at id.atlassian.com and enter it as the new API token in Edit (for an older setup, update the environment variable or keychain entry), then Sync now.
- **A sync that is already waiting for approval because of this is now safe to approve or cancel.** Approving plans the sync again: it finds the rejected token and deletes nothing.
- **The Jira project picker and the preview use the same check.** Before, the picker said "This account can't see any Jira project" when the token was rejected.
- A Jira query that really matches nothing still asks before deleting files, as before.

## Usage data (only if you chose "Share usage")

- No new events. A sync stopped by a rejected token is reported with the existing `sync_failed` event and the error class `auth`.
