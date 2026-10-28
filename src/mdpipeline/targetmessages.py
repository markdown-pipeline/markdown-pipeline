"""Needs-you copy for target failures (MD-68, MD-78): a title and a next step per failure category. Plain words, never a secret."""
from __future__ import annotations

NEXT_STEPS = {
    "auth": ("GitHub rejected the credential", "Open the pipeline's target settings and enter a new token (or sign in again), then Sync now."),
    "protected-branch": ("The branch is protected", "The pipeline commits directly. Use a dedicated branch such as 'md-sync' (create it in the "
                         "repository, then change the target of the pipeline), or allow this credential to push to the branch."),
    "head-moved": ("The branch kept changing", "Other people or agents committed while the sync ran, so nothing was written. Try again in a moment."),
    "rate-limited": ("GitHub rate limit reached", "Nothing was written. Wait a while (the limit resets within the hour), then Sync now."),
    "network": ("GitHub could not be reached", "Nothing was written. Check the network, then try again; the next sync converges."),
    "sign-in-expired": ("Sign in with GitHub again", "Your GitHub sign-in expired or was revoked. Nothing was written or deleted. Press "
                        "Sign in again, or run `mdpipeline pipeline sign-in NAME`; then Sync now."),
    "vault-locked": ("The vault is locked", "The app could not open its vault, so nothing was written or deleted. If it came from an earlier version, enter its passphrase "
                     "once (in the UI, or run `mdpipeline vault status`): your tokens then move to a file only you can read and it never asks again. Otherwise restore "
                     "the OS keychain entry. Then Sync now. Pipelines that need no secret keep syncing."),
    "vault-unsafe": ("The saved-secrets file is open to other users", "The app will not read a file that other users can read, and changed nothing. Run `mdpipeline vault status` "
                     "to see the exact command to fix its permissions (chmod 600 on the file), then Sync now. Pipelines that need no secret keep syncing."),
    "credential-conflict": ("Two pipelines use different credentials for one repository and branch", "Nothing was written or deleted, and these pipelines do not sync "
                            "until this is resolved. Give them the same credential (Edit the target and use the same token or sign-in), or move one of them to another branch."),
    "error": ("The repository could not be read or written", "Nothing was written. Use Test connection on the pipeline's target to see what is wrong."),
}
CLONE_NEXT_STEP = ("Commit or stash your changes in the clone (or fix its remote or branch), then Sync now. "
                   "The pipeline never touches local work: the clone is only fast-forwarded when that is safe.")


def with_hint(message: str, category: str, pipeline: str) -> str:
    """Add the command that fixes the failure when there is one that names the pipeline."""
    if category == "sign-in-expired" and "pipeline sign-in" not in message:
        return f"{message} Run: mdpipeline pipeline sign-in {pipeline}"
    return message
