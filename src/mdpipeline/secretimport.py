"""Import the old ``env:`` / ``keychain:`` credential references (GitHub targets MD-87, Jira sources MD-88) into the app vault, once.

Until a reference is imported it keeps working exactly as before (nothing breaks on update). Import reads the secret through the
old reference one last time, keeps it in the vault, and rewrites the pipelines that used it to ``vault:<id>``. From then on the
environment variable or keychain entry is never read for that target again, and the person is told they may delete it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import vault as vault_mod
from .brain import Brain
from .config import load_config, save_config
from .credentials import CredentialError, parse_ref, resolve_token
from .destinations import destination_type
from .locking import brain_lock
from .log import ActivityLog


@dataclass
class PendingImport:
    ref: str
    pipelines: list[str] = field(default_factory=list)
    secret_kind: str = "github-token"       # what the vault keeps it as: github-token, jira-token or jira-email

    @property
    def kind(self) -> str:
        return self.ref.partition(":")[0]

    @property
    def name(self) -> str:
        return self.ref.partition(":")[2]


@dataclass
class ImportResult:
    ref: str
    pipelines: list[str]
    ok: bool
    message: str
    secret_id: str = ""

    def to_dict(self) -> dict:
        return {"ref": self.ref, "pipelines": self.pipelines, "ok": self.ok, "message": self.message, "secret_id": self.secret_id}


def _legacy(ref: str) -> bool:
    try:
        return parse_ref(ref)[0] in ("env", "keychain")
    except CredentialError:
        return False


def _uses(pipeline):
    """``(reference, secret kind)`` of every credential reference of the pipeline: its GitHub target and its Jira sources."""
    if destination_type(pipeline.destination) == "github":
        yield (pipeline.destination or {}).get("credential", ""), "github-token"
    for source in pipeline.sources:
        if source.type == "jira":
            yield source.options.get("email_ref", ""), "jira-email"
            yield source.options.get("token_ref", ""), "jira-token"


def pending_imports(brain: Brain) -> list[PendingImport]:
    """Credentials (GitHub token, Jira email and API token) that still use an ``env:`` or ``keychain:`` reference, grouped by reference."""
    groups: dict[tuple[str, str], PendingImport] = {}
    for p in load_config(brain).pipelines:
        for ref, kind in _uses(p):
            if ref and _legacy(ref):
                item = groups.setdefault((ref, kind), PendingImport(ref, [], kind))
                if p.name not in item.pipelines:
                    item.pipelines.append(p.name)
    return list(groups.values())


def _advice(item: PendingImport) -> str:
    where = f"the environment variable {item.name} (and any .env entry for it)" if item.kind == "env" else f"the OS keychain entry '{item.name}'"
    return (f"Imported {item.ref} ({item.secret_kind}) for {', '.join(item.pipelines)} into the vault. The app will not read it again: you may delete {where}.")


def import_references(brain: Brain, trigger: str = "cli") -> list[ImportResult]:
    """Import every pending reference. A locked vault stops the import before anything changes (``VaultLocked``)."""
    items = pending_imports(brain)
    if not items:
        return []
    vault = vault_mod.default_vault()
    vault.list()                                               # raises VaultLocked early: nothing has changed yet
    results: list[ImportResult] = []
    with brain_lock(brain):
        for item in items:
            try:
                token = resolve_token(item.ref)
            except CredentialError as exc:
                results.append(ImportResult(item.ref, item.pipelines, False, f"{exc} Nothing was changed for {', '.join(item.pipelines)}; "
                                            "the old reference stays until you fix it or enter the token in the pipeline's target settings."))
                continue
            label = {"github-token": "GitHub token", "jira-token": "Jira API token", "jira-email": "Jira email"}[item.secret_kind]
            secret_id = vault.add(item.secret_kind, f"{label} imported from {item.ref}", token)
            config = load_config(brain)
            for p in config.pipelines:
                if item.secret_kind == "github-token":
                    if (p.destination or {}).get("credential") == item.ref:
                        p.destination["credential"] = f"vault:{secret_id}"
                else:
                    key = "email_ref" if item.secret_kind == "jira-email" else "token_ref"
                    for source in p.sources:
                        if source.type == "jira" and source.options.get(key) == item.ref:
                            source.options[key] = f"vault:{secret_id}"
            save_config(brain, config)
            brain._cache.clear()
            ActivityLog(brain).event("import_secret", None, trigger, pipelines=item.pipelines, kind=item.kind)
            results.append(ImportResult(item.ref, item.pipelines, True, _advice(item), secret_id))
    return results
