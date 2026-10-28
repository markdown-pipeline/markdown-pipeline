# Register the GitHub App (owner action, MD-77)

Time: about 5 minutes. You do this once, on the GitHub account that will own the app. Nothing here is a secret except
what is marked, and the app needs **only its client ID**.

The file `github-app-manifest.json` next to this document is the source of truth for the values. The GitHub manifest flow
needs a web server to receive a redirect, which a local app does not have, so register the app by hand in the settings
form and copy the values from the manifest.

## Steps

1. Sign in to GitHub as the account that should own the app. Open **Settings > Developer settings > GitHub Apps > New GitHub App**.
2. **GitHub App name:** `Markdown Pipeline` (any free name; the URL slug follows from it, note it down).
   **Homepage URL:** the `url` in the manifest. **Description:** the `description` in the manifest.
3. **Identifying and authorizing users:**
   * **Callback URL:** leave it empty if GitHub allows it, otherwise enter `http://127.0.0.1/` (it is never used by the device flow).
   * Leave **Expire user authorization tokens** ticked (recommended; see "The refresh question" below for the fallback).
   * Untick **Request user authorization (OAuth) during installation**.
   * Tick **Enable Device Flow**. This is not in the manifest and is the one setting without which sign-in fails with `device_flow_disabled`.
4. **Post installation:** leave everything empty.
5. **Webhook:** untick **Active**. The app has no webhook and needs no events.
6. **Permissions > Repository permissions:** **Contents: Read and write**, **Metadata: Read-only** (it is always on). Nothing else.
   No organization or account permissions.
7. **Where can this GitHub App be installed?** choose **Only on this account** (the manifest has `public: false`). For other
   people's use later this changes to Any account; that is a hosting decision, not part of this task.
8. Click **Create GitHub App**.
> **Shipped default.** The app already ships with the owner's registered GitHub App (client ID and slug in `src/mdpipeline/defaults.json`), so a normal user needs none of the steps in this document. They are for registering **your own** app. What you save (step 9) wins over the shipped default; `mdpipeline github-app show` and the UI say which one is in use, and `mdpipeline github-app reset` (or "Use the shipped default" in the UI) goes back to it.

9. On the app's page copy the **client ID** (looks like `Iv1.0123456789abcdef`, or `Iv23...`) and note the app **slug** (the last part of
   `https://github.com/apps/<slug>`). **Do not** generate a client secret or a private key: the app never uses them. Enter both in the app: in the
   UI, editor, "Where the files land", GitHub repository, Sign in with GitHub, fields "Client ID" and "App slug" (Save; no restart), or in a terminal
   `mdpipeline github-app set --client-id ID --slug SLUG`. They are public values, kept in the app data directory (not in the vault).
10. **Install App** (left menu) > install on your account > **Only select repositories** > pick the **scratch repository**
    (a throw-away repo, never the real brain repo) > Install.

## What to send the developer

* the client ID
* the app slug (the last part of `https://github.com/apps/<slug>`)
* the scratch repository name as `OWNER/REPO` and its default branch, plus (for the token checks of MD-73) a fine-grained
  personal access token for that one repository with Contents read/write, **given only on the machine, never in chat or in a file**:
  enter it in the app (`mdpipeline pipeline test-target`) once the vault exists.

## The refresh question (H-PT-4)

GitHub documents that refreshing a user token takes the client secret. The app has no secret to ship, so the app tries the
refresh with the client ID and the refresh token only. If GitHub refuses that (`incorrect_client_credentials`), the app
treats it as "Sign in again" (no data is lost, nothing is written). **Fallback if that happens in the live check:** on the app
settings page untick **Expire user authorization tokens**; user tokens then do not expire and no refresh is needed
(they can still be revoked by you at github.com/settings/applications).

## Live check once registered (tell the developer the result)

1. `mdpipeline pipeline add scratch --source ... --github OWNER/REPO --auth sign-in` shows a code and `https://github.com/login/device`.
2. Enter the code on GitHub, approve. The command says "Signed in as <your login>".
3. The repository list shows the scratch repo; the other repos are not listed.
4. A sync commits one commit to the scratch repo; the commit author is you.
