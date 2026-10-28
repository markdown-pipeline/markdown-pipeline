# Scoped read-only Atlassian API tokens spike (MD-127 / FR-T2)

Date: 2026-10-08. Timebox: ½ day. Status: **answered by two live runs** (owner, real Jira Cloud site, `scripts/live_scoped_token_check.py`).
An earlier run was discarded by the owner (token mistake) and is not used here.

Question: can our Jira reads (`/myself`, `search/jql`, `search/approximate-count`, `issue/{id}`, `issue/{id}/comment`) run with a scoped,
read-only token, which scopes, and what changes in the connector?

## Answer

**Yes, through the gateway, with `read:jira-work` alone for everything the sync reads.**

| Call | `read:jira-work` + `read:jira-user` | `read:jira-work` only |
|---|---|---|
| gateway `GET /myself` | 200 | **401** |
| gateway `GET /search/jql` | 200 | 200 |
| gateway `POST /search/approximate-count` | 200 | 200 |
| gateway `GET /issue/{id}` | 200 | 200 |
| gateway `GET /issue/{id}/comment` | 200 | 200 |
| gateway `POST /issue` with an empty body (write probe; cannot create anything) | **401** | **401** |
| site URL `GET /myself` | 401 | 401 |
| site URL `GET /search/jql` | 200 (body not inspected) | 200 (body not inspected) |
| site URL `POST /search/approximate-count` | 200 | 200 |
| site URL `GET /issue/{id}` (an id the gateway reads fine) | **404** | **404** |
| site URL `GET /issue/{id}/comment` | **404** | **404** |

1. **Scopes.** `read:jira-work` is enough for search, count, issue and comments. `/myself` additionally needs `read:jira-user`. The connector
   uses `/myself` only to test the connection and show a name, so it can test with a one-result `search/jql` instead and skip `read:jira-user`
   (smallest possible scope), or keep `/myself` and ask for both scopes. Product's choice; engineering recommends the smaller scope.
2. **The gateway is required.** On the site URL a scoped token is refused or blind: `/myself` 401, existing issues 404. Auth is Basic
   `email:token` on `https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/...` (confirmed in Atlassian's help page and by the runs).
3. **Hazard: the site URL answers 200 to a search with a scoped token while it cannot read the issues.** The response body was not
   inspected, but a 200 search next to 404 issues is the pattern of an empty or partial listing. The connector treats an id missing from a
   complete listing as deleted, so a scoped token sent to the site URL could look like "everything was removed". The connector must therefore
   **never send a scoped token to the site URL** (a token kind in the vault entry decides the base URL; the guard already asks on an empty
   listing, but this must not rely on that).
4. **Read-only.** The gateway refuses `POST /issue` with 401 for both tokens, so a scoped read token is refused writes at the credential level.
   Only one write endpoint was probed; the claim rests on that plus the scope model (the token carries no `write:` scope). Safe user-facing
   wording: "a token limited to read access; the app only reads either way". The stronger "read-only at the credential level" is supported,
   with this one-endpoint evidence stated in the doc, if marketing wants it (decision for product/marketing: H-FR-4).
5. **Expiry.** Scoped tokens expire (1 to 365 days, default one year). FR-S5 needs an "expired token, create a new one" error and the expiry
   advice: choose the longest period you are comfortable with.
6. **Default first-run scope** (last part of the question): `updated >= -90d ORDER BY updated DESC` on the projects the user picks. It uses only
   calls already in the allow-list.

## Connector changes required (a follow-up story; not done in this spike)

1. `jira_api.py` host rule: today only the configured site host is allowed. A scoped token needs `api.atlassian.com`, restricted to the exact
   path prefix `/ex/jira/{cloudId}/rest/api/3/` and the same GET/POST allow-list. A classic token keeps the site URL.
2. Resolve `cloudId` from the site: `GET https://<site>.atlassian.net/_edge/tenant_info` (unauthenticated; returned 200 with a `cloudId` in
   the live run). Store it with the source.
3. Token kind (classic / scoped) in the vault entry, so a scoped token is never sent to the site URL (finding 3).
4. Test connection without `/myself` for scoped tokens (finding 1), and errors for "scoped token on the site URL" and "token expired".
5. `tests/fake_jira.py` needs a gateway mode: scoped token refused on the site URL (404 for issues, search answering 200 but empty, so the
   fail-closed behaviour is tested), cloudId required, expiry.

## Sources
* Atlassian Account help, Manage API tokens (scoped tokens, auth, base URL, expiry): https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/
* Live runs with `scripts/live_scoped_token_check.py` (2026-10-08, owner).
