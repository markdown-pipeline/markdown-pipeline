# GitHub App sign-in spike (MD-76): device flow for a local app

Status: written from GitHub's documentation (Authenticating to the REST API with a GitHub App on behalf of a user; Refreshing
user access tokens; Managing GitHub Apps; REST: `GET /user/installations`). **Nothing here was verified live**: there is no
registered app yet (MD-77 is the owner's action) and no network use in this task. Every "to verify" is item 1-4 of the live
check in `github-app-registration.md`. The behaviour is encoded in `tests/fake_github.py` (sign-in section) and
`tests/test_fake_github_signin.py`; the app code is built against that fake (MD-83, MD-84).

## 1. Device flow (question 1)
1. `POST https://github.com/login/device/code` with `client_id` (send `Accept: application/json`) -> `device_code`, `user_code`,
   `verification_uri` (`https://github.com/login/device`), `expires_in` (900 s), `interval` (5 s).
2. The app shows the user code and the link. The person types the code on github.com and approves the app.
3. The app polls `POST https://github.com/login/oauth/access_token` with `client_id`, `device_code`,
   `grant_type=urn:ietf:params:oauth:grant-type:device_code` every `interval` seconds. **Errors come back as HTTP 200 with an
   `error` field**:
   `authorization_pending` (keep polling), `slow_down` (add 5 s: the response carries the new `interval`), `expired_token`
   (start again), `access_denied` (the person said no), `incorrect_device_code`, `incorrect_client_credentials` (wrong client id),
   `device_flow_disabled` (the "Enable Device Flow" checkbox is off).
4. Success: `access_token` (`ghu_...`), and when the app has "Expire user authorization tokens" on: `expires_in` (28800 s = 8 h),
   `refresh_token` (`ghr_...`), `refresh_token_expires_in` (about 6 months).
No client secret is used anywhere in the device flow.

## 2. Refresh without a client secret (question 2, H-PT-4) - UNVERIFIED, the main risk
Documented refresh: `POST https://github.com/login/oauth/access_token` with `client_id`, `grant_type=refresh_token`,
`refresh_token`, and **`client_secret`**. The docs list the secret as required. Whether a device-flow app can omit it is
the open question; community reports say it can, GitHub's page does not promise it.

Decision (default, reversible): the app **tries the refresh without a secret** and never ships one. Outcomes:
* it works: tokens rotate (the refresh token is single use, the new pair replaces the old one in the vault atomically);
* `incorrect_client_credentials` / `bad_refresh_token`: the sign-in is treated as expired -> "Needs you: Sign in again"; nothing
  is written or deleted (MD-84). The owner can also untick **Expire user authorization tokens** in the app settings (see
  `github-app-registration.md`), then there is nothing to refresh.
Both outcomes are tested against the fake (`refresh_requires_secret`). Product decision needed only if the live check says
"needs a secret" AND the owner does not want non-expiring tokens.

## 3. Installed repositories (question 3)
* `GET /user/installations` (user token) -> installations of this app the person can access, each with `permissions`.
  A personal access token gets 403 here.
* `GET /user/installations/{id}/repositories` -> repos selected for that installation.
* "App not installed on this repo": a user token sees only repos the app is installed on, so `GET /repos/{o}/{r}` answers **404**
  for a repo without the app (also for private repos in general). The app maps 404 + sign-in auth to "install the app on this
  repository" with the link `https://github.com/apps/<slug>/installations/new` and re-checks on request.

## 4. Effective permissions (question 4)
A user-to-server token has the intersection of the person's rights and the app's permissions. `repo.permissions.push` in
`GET /repos/{o}/{r}` reflects the **person's** role, not the app's, so it is **not enough** for sign-in tokens. The write check
for sign-in is: `permissions.push` AND the installation's `permissions.contents == "write"` (from `/user/installations`).
Test connection reports the missing side in plain words. For a PAT the existing `push` check stays.

## 5. Commit author and signed commits (question 5)
Commits created through the Git Data API (`POST /git/commits` with only message, tree, parents) are authored by the token's
user, for a user-to-server token as for a PAT. They are not signed. A branch that requires signed commits refuses the ref update;
the existing protected-branch message covers it (suggest a dedicated branch). Test connection reports the author login.

## 6. Sign out and revoke (GA-S2 AC3)
Revoking a token (`DELETE /applications/{client_id}/token`) needs the client secret (basic auth), which the app does not have.
So **sign out removes the tokens from the vault only**; the person can revoke the app at github.com/settings/applications.
The UI/CLI say so in plain words.

## 7. Network surface (D-PT-16)
Sign-in adds exactly: `https://github.com/login/device/code` and `https://github.com/login/oauth/access_token`. Everything else
stays on `api.github.com`. Both are https and allow-listed; the sign-in module has its own allow-list of those two paths.

## 8. Manifest and registration (question 6, MD-77)
`github-app-manifest.json` has the name, description, `contents: write`, `metadata: read`, no events and no webhook. Two
settings the manifest cannot carry: **Enable Device Flow** (must be on) and the token-expiry checkbox. The manifest flow needs a
redirect server; a local app has none, so the owner registers through the form with the same values:
`github-app-registration.md` is the step-by-step.

## 9. Risks posted on the epic
1. H-PT-4 (refresh without a secret) unverified; fallback documented.
2. The `push` permission is not a reliable write check for sign-in tokens (use the installation's contents permission).
3. 404 is the only signal for "app not installed"; it is also what a typo in the repo name gives. The message names both.
4. Revocation cannot be done by the app (no secret).
5. Signed-commit requirements are not satisfiable with this API path.
6. All behaviours above are from documentation; only the live check closes them.
