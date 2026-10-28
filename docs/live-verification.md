# Live verification runbook (MD-73 GitHub, MD-74 Jira, MD-77/MD-83 sign-in)

The whole test suite runs against fakes (`tests/fake_github.py`, `tests/fake_jira.py`, a fake keychain). Nothing in it has touched a real service.
These three scripts and checklists are how the fakes get checked against the real thing. They need things only the owner can provide.
**Never use the owner's real brain repository or a production Jira project for the GitHub write checks.**

## A. GitHub target (MD-73): what the owner provides
1. A **scratch private repository** (throw-away, with a `main` branch and at least one commit), e.g. `OWNER/mdp-scratch`.
2. A **fine-grained personal access token** for that one repository, Contents: Read and write.
3. Optional, for the protected-branch checks: a branch (e.g. `protected`) with a branch-protection rule that blocks direct pushes.
4. Optional, for the read-only-token check: a second token with Contents **read-only** on the same repository.

Run (the token is typed hidden; or pipe it with `--stdin`; or keep it in the vault and pass `--use-secret ID`):
```bash
cd app
uv run python scripts/live_github_check.py --repo OWNER/mdp-scratch --scratch-repo-confirmed --protected-branch protected --report /tmp/github-live.json --cleanup
```
It creates a work branch `mdp-live-check-<time>` from the default branch (everything is written there; the default branch is never changed), runs
the checklist and prints PASS / FAIL / MANUAL:

| Item | What it checks | How |
|---|---|---|
| 1a | test connection against the real repo: user, default branch head, `permissions.push`, response keys the target relies on | automated |
| 1b, 1b2 | a protected branch is refused with the `md-sync` advice; GitHub really refuses a ref update with a message containing "protect" | automated with `--protected-branch` |
| 1c | a read-only token is refused ("read and write") | MANUAL: re-run with the read-only token |
| 1d | a missing repo is 404, a bad token is 401 | automated |
| 2a-2d | one commit for add/update/delete/rename, none for no change, the state file in the repo holds no content and no secret | automated |
| 3, 3b | a teammate's commit mid-sync is kept (retry, linear history, no force); a non-fast-forward update is refused with a "fast forward" message | automated |
| 4a, 4b | an owned file edited in the repo is a conflict and kept; one deleted in the repo is recreated | automated |
| 5a, 5b | a 500-file first sync and a 500-file dry run: time, number of requests, rate-limit headers present | automated (`--files N`) |
| 5c, 5d | tree truncation (100,000 entries) and a real rate-limit response | MANUAL (cannot be provoked cheaply) |
| 6a | an SSH-origin clone is refused with a reason | automated (local, no network) |
| 6b | an https clone is fast-forwarded after a sync | MANUAL: the exact commands are printed |

**Reading the result.** Every FAIL, and every `DIVERGENCE:` line, is a difference between real GitHub and the fake. File each as an MD bug, correct
`tests/fake_github.py`, and fix the target if its behaviour depends on the wrong assumption. Write PASS/FAIL per item on MD-73. Done = every item has
a result and no divergence is left open.

## B. Sign in with GitHub (MD-77 -> MD-83, MD-84): what the owner provides
Register the GitHub App with `docs/github-app-registration.md` (5 minutes) and send: the **client ID**, the **app slug**, and the scratch repository
the app is installed on. Then:
```bash
uv run mdpipeline github-app set --client-id Iv1.xxxx --slug my-app-slug
uv run mdpipeline init ~/mdp-live --create
uv run mdpipeline pipeline add live --source ~/some/docs --github OWNER/mdp-scratch --auth sign-in      # shows the code and the link
```
Checks to write on MD-83/MD-84: (1) the code + link work and "Signed in as <you>" appears; (2) only the installed repo is listed; (3) a sync commits
as you; (4) **the refresh question (H-PT-4)**: after 8 hours (or by setting the token expiry short in a test app) a sync still works without a
client secret, or it shows "Sign in again" and you untick "Expire user authorization tokens" (see the registration doc); (5) "Require signed commits"
on the branch refuses the commit with the protected-branch message; (6) uninstalling the app gives "Sign in again" and changes nothing in the repo.

## C. Jira connector (MD-74): what the owner provides
The Jira site host (`https://NAME.atlassian.net`), the account email, a **read-only** API token (id.atlassian.com, Security, API tokens), and permission to
read projects MD and MP. Nothing is ever written to Jira (the client cannot send anything but the allow-listed reads).

```bash
cd app
uv run python scripts/live_jira_check.py check  --site https://NAME.atlassian.net --email you@company.com --jql "project in (MD, MP)" --report /tmp/jira-live.json
uv run python scripts/live_jira_check.py record --site https://NAME.atlassian.net --email you@company.com --jql "project in (MD, MP)" --out tests/fixtures/jira/real --limit 25
uv run python scripts/live_jira_check.py perf   --site https://NAME.atlassian.net --email you@company.com --jql "project in (MD, MP)" --changed 20 --backfill 500
```
* `check`: auth (and a wrong token is `auth`), listing with pages of 100 and of 7 (must be identical), approximate count, 25 issues + comments rendered
  (conversion warnings, the ADF node types seen, **any node type the converter does not handle is a divergence**), a first sync and a no-change plan.
  MANUAL items J6-J9 (delete an issue, move one between projects, lose permission, a real 429) are written out with the steps.
* `record`: writes a **sanitised** corpus (people, e-mails, account ids, urls and all free text replaced; structure kept; the script refuses to call it
  clean if the email, token or site host is still in a file). Review `tests/fixtures/jira/real/` before committing; then add golden tests for any new
  node type.
* `perf` (MD-50 AC5): P1 = time to re-read 20 changed issues (target < 30 s); P2 = a killed backfill of about 500 issues resumes with no duplicate or lost
  file (if the JQL matches far more, it asks for a narrower one). The same P2 proof already passes against the fake with 482 issues
  (`tests/test_live_jira_harness.py`), so a real site only adds the latency numbers.

Write PASS/FAIL per item and the measured numbers on MD-74 (Product persists them).

## D. What the scripts never do
They take no token as an argument (a test enforces it), print no secret, change nothing in GitHub except the work branch they create, change nothing in Jira,
and are not run by the test suite against a real service.
