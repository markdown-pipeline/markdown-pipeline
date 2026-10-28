"""MD-134: remove a pipeline from the UI. The same removal as ``mdpipeline pipeline remove`` (removal.remove_pipeline): the files are kept by
default (they become unowned), or the unedited files the pipeline created are deleted after a confirmation that names the exact count.

Credentials: a secret the pipeline referred to leaves the vault only when no other pipeline uses it, and the answer says which is which
(names only, never a value). A locked vault never blocks a removal: its secrets simply stay where they are."""
from __future__ import annotations

from . import vault as vault_mod
from .brain import Brain
from .config import load_config
from .removal import ConfirmationRequired, preview_remove_pipeline, remove_pipeline


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _secret_ids(pipeline) -> list[str]:
    ids = [ref[len("vault:"):] for ref in vault_mod._refs(pipeline) if ref.startswith("vault:")]
    return list(dict.fromkeys(ids))


def _credential_plan(brain: Brain, pipeline) -> dict:
    """Which of the pipeline's secrets would be removed from the vault and which stay because another pipeline uses them."""
    others = [p for p in load_config(brain).pipelines if p.name != pipeline.name]
    removed, kept = [], []
    try:
        names = {i.id: i.name for i in vault_mod.default_vault().list()}
    except vault_mod.VaultLocked:
        return {"credentials_removed": [], "credentials_kept": [], "_ids_to_remove": []}
    ids_to_remove: list[tuple[str, str]] = []
    for secret_id in _secret_ids(pipeline):
        if secret_id not in names:
            continue
        users = [p.name for p in others if f"vault:{secret_id}" in vault_mod._refs(p)]
        if users:
            kept.append({"name": names[secret_id], "used_by": users})
        else:
            removed.append(names[secret_id])
            ids_to_remove.append((secret_id, names[secret_id]))
    return {"credentials_removed": removed, "credentials_kept": kept, "_ids_to_remove": ids_to_remove}


def preview(brain: Brain, name: str) -> dict:
    """What removing would do, with exact numbers. Writes nothing."""
    pipeline = load_config(brain).get(name)
    numbers = preview_remove_pipeline(brain, name)
    plan = _credential_plan(brain, pipeline)
    plan.pop("_ids_to_remove")
    return {"name": name, "owned": numbers.owned, "deletable": numbers.deletable, "edited": numbers.edited, **plan}


def remove(brain: Brain, name: str, body: dict) -> dict:
    """Remove the pipeline. ``delete_files`` + ``confirm_count`` (the count the user was shown) delete the files it created."""
    from . import usage_hooks

    pipeline = load_config(brain).get(name)
    delete = bool(body.get("delete_files"))
    count = body.get("confirm_count") if delete else None
    plan = _credential_plan(brain, pipeline)
    result = remove_pipeline(brain, name, trigger="ui", purge=delete, confirm_count=count)        # raises ConfirmationRequired
    for secret_id, secret_name in plan.pop("_ids_to_remove"):
        try:
            vault_mod.default_vault().remove(secret_id)
        except vault_mod.VaultError:                  # includes a vault that locked meanwhile: the secret stays, and we do not claim otherwise
            plan["credentials_removed"].remove(secret_name)
    usage_hooks.pipeline_removed(pipeline)
    if delete:
        kept = len(result.left_edited)
        message = f'"{name}" removed. {_plural(result.deleted, "file", "files")} deleted'
        if kept:
            message += f"; {kept} kept because you edited {'it' if kept == 1 else 'them'}"
        message += "."
    else:
        kept = result.unowned
        message = f'"{name}" removed. {_plural(kept, "file", "files")} kept in your folder.'
    return {"removed": True, "deleted": result.deleted, "kept": kept, "edited": result.left_edited, "message": message, **plan}


__all__ = ["ConfirmationRequired", "preview", "remove"]
