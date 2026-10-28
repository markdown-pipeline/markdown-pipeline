# Troubleshooting: Jira token

Markdown Pipeline reads your Jira Cloud issues, comments and statuses. It never changes anything in Jira.
Your token stays on this computer and is only sent to Atlassian.

## Create the token (about 2 minutes)

1. Open https://id.atlassian.com/manage-profile/security/api-tokens while signed in to the Atlassian account you use for Jira.
2. Choose **Create API token** (not "Create API token with scopes": scoped tokens are not supported yet).
3. Name it "Markdown Pipeline" and copy the token. Atlassian shows it once.
4. Paste it in the app together with your Jira site and the email of that same account.

## If the test fails

- **We couldn't find a Jira site at ...**: use the part before `.atlassian.net` in the address you open Jira with, or paste the full address.
- **Atlassian didn't accept this email and token together**: Atlassian gives the same answer for a wrong email and for a bad token. Check that the email belongs to the account that created the token, that the whole token was copied, and that it hasn't expired or been revoked. If unsure, create a new token.
- **This account can't see any Jira project**: ask a Jira admin for "Browse projects" access, or use another account.
- **Jira Data Center or Server**: early access supports Jira Cloud only.
- **No internet connection / Atlassian is not available**: nothing was saved. Check your connection or try again in a few minutes.
