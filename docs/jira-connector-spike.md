# Jira connector spike (MD-44): Jira Cloud read API, deletion and key-change detection, fixture corpus

Status: written from Atlassian's published Jira Cloud REST v3 and ADF documentation. It has **not** been checked against a live
site: no API token was available to the developer agent when this was written, and the Atlassian tools available in the session
return processed data, not the raw REST JSON the connector consumes. Everything marked "verify" is the first thing to try with a
real token (`mdpipeline` will use the same calls). The behaviour described here is what `tests/fake_jira.py` implements and what every
Jira test runs against. The fixture corpus (`tests/jira_fixtures.py`) is **synthetic** (built from the published schemas), not recorded
from the owner's MD and MP projects.

## Endpoints (all read-only)
| Need | Call | Notes |
|---|---|---|
| Who is the token | `GET /rest/api/3/myself` | `displayName` for test-connection. 401 = bad credentials. |
| Scope listing | `GET /rest/api/3/search/jql?jql=&fields=&maxResults=&nextPageToken=` | The enhanced JQL search: cursor pagination (`nextPageToken`, `isLast`), at most 100 per page. The old `/rest/api/3/search` (startAt) is being removed and is not used. |
| How many match | `POST /rest/api/3/search/approximate-count` `{jql}` | Read-only despite POST; the only POST the connector may send (an allow-list test). |
| One issue | `GET /rest/api/3/issue/{id}?fields=*all` | The description is ADF (v3). Fetch **by id** so a moved issue is still the same item. |
| Comments | `GET /rest/api/3/issue/{id}/comment?startAt=&maxResults=100` | Paginate with `total`; the `comment` field of the issue holds only a first page. Bodies are ADF. |
| JQL validity | the search call itself | A bad JQL answers 400 with `errorMessages` (Jira's own text, shown to the user). |

Authentication: HTTP Basic `email:API token` (classic API token) against `https://<site>.atlassian.net`. Scoped tokens and OAuth go through
`api.atlassian.com/ex/jira/<cloudId>`, a different host: out of scope (the network rule allows only the configured site host).

## Incremental sync and "changed since"
JQL time filters (`updated >= "YYYY-MM-DD HH:mm"`) have minute granularity and clock/time-zone traps, so the connector does not use them.
Instead each sync lists the scope with `fields=updated` (id, key, updated: cheap, ~100 per request), compares each issue's `updated` with
the version recorded in the state, and fetches full content (issue + all comment pages) **only for new or changed issues**. A change
includes new, edited and deleted comments, links and sub-task changes: Jira bumps `updated` for all of these (verify, esp. comment
deletion). The version also embeds the renderer version, so a renderer upgrade rewrites everything once. Nothing moves forward unless the
whole sync succeeded (state is written only with the files).

## Deletions, out-of-scope issues and key changes
* The listing is the only truth: an owned issue id absent from a **complete, successful** listing is gone (deleted, moved out of the JQL,
  or no longer visible to this user: Jira answers the same for all three; documented limit).
* Any failure (HTTP error, any page failing, malformed JSON, timeout, auth) marks the whole source failed: no entries, status
  `unreadable`, so the planner never deletes (MD-52 AC2).
* A scope that returns 0 issues while the source owns files is suspicious: the guard always asks.
* Identity is the numeric issue **id**, which is stable. A project move changes the **key** (the old key still resolves by REST), so the
  file keeps its name; its directory follows the `{dir}` template variable (project key), which the planner reports as one `rename`
  (not delete + create). Summary changes do not rename (names are decided once).

## Limits and rate limiting
* Page size up to 100 for search; comments page size 100. A 500-issue backfill is about 5 listing requests + 500 issue requests + comment
  pages; resumable because files and state are committed per sync (a killed first sync just starts over, already-synced items are skipped
  by version).
* Rate limits: HTTP 429 with `Retry-After` (seconds). The client waits (capped) and retries a bounded number of times, then fails the source
  without deleting anything (verify the header on real 429s; Jira also sends `X-RateLimit-*`/`RateLimit-Reason`).
* Time stamps carry an offset (`+0000`/`+0300`): everything is converted to UTC; `{date}` is the created date in UTC.

## ADF node types handled (fixture corpus)
Blocks: paragraph, heading 1-6, bulletList/orderedList/listItem (nested), taskList/taskItem, decisionList/decisionItem, codeBlock (language),
blockquote, panel (info/note/warning/success/error), rule, table (tableRow/tableHeader/tableCell), mediaSingle/mediaGroup/media, expand,
nestedExpand, blockCard, layoutSection/layoutColumn. Inline: text, hardBreak, mention, emoji, date, status, inlineCard, mediaInline. Marks:
strong, em, strike, code, underline, link, subsup, textColor and others (kept as plain text). Any other node: its text is kept with a visible
marker and the item is counted as a conversion warning.

## Risks the stories do not cover
1. **No live verification** (above); field shapes (sprint custom field id differs per site: found by shape, `customfield_10020` by default).
2. **Scoped API tokens** use a different host and would be refused by the host rule.
3. **Comment/permission edge cases:** comments restricted to a role are simply not returned; a permission change looks like a deletion.
4. **Display names change** when a user renames: a metadata-only rewrite of every issue that person touched (MD-56 makes the guard ignore it).
5. **Rendering is the contract:** a renderer change rewrites every Jira file once (the renderer version is part of the version string).
6. **Large descriptions/comment threads** make big files; no truncation is applied (a file is never silently shortened).
7. **IP allow-lists / SSO-only sites** make API-token calls fail with 401/403, reported as auth.
