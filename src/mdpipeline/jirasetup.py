"""Setting up a Jira source's credentials (MD-88): the email and API token are checked against Jira first, then kept in the vault.

Typed values travel in memory only (``_email`` / ``_token`` options of the in-memory source, never persisted) until a check passes;
the configuration then holds ``vault:<id>`` for both. A token that does not pass is never stored.
"""
from __future__ import annotations

from . import vault as vault_mod
from .config import Pipeline, Source
from .credentials import CredentialError, parse_ref, resolve_token
from .jira_api import JiraApi
from .jira_source import JiraSource, check_jira_source
from .service import ValidationFailed
from .sources import SourceError
from .validation import Issue, ValidationResult


def _fail(code: str, message: str, field: str = "") -> ValidationFailed:
    return ValidationFailed(ValidationResult(errors=[Issue(code, message, field)]))


def is_reference(text: str) -> bool:
    try:
        parse_ref(text)
        return True
    except CredentialError:
        return False


def check_values(source: Source, email: str | None, token: str | None) -> dict:
    """Test the site, the account and the JQL with the given email/token (each falling back to the source's saved reference).
    Read-only. Raises ``ValidationFailed`` with Jira's plain message."""
    options = dict(source.options)
    placeholder = {**options, "email_ref": options.get("email_ref") or "vault:v_checking", "token_ref": options.get("token_ref") or "vault:v_checking"}
    static, _ = check_jira_source(Source(source.path, source.label, type="jira", options=placeholder), False)
    if static:
        raise ValidationFailed(ValidationResult(errors=[Issue(code, message, "sources") for code, message in static]))
    try:
        api = JiraApi(options["site"], email if email else (lambda: resolve_token(options["email_ref"])),
                      token if token else (lambda: resolve_token(options["token_ref"])))
        return JiraSource(source, api=api).test_connection()
    except SourceError as exc:
        raise _fail("jira-" + exc.category, str(exc), "sources") from None
    except CredentialError as exc:
        raise _fail("jira-auth", str(exc), "sources") from None


def has_typed(pipeline: Pipeline) -> bool:
    return any(s.type == "jira" and (s.options.get("_email") or s.options.get("_token")) for s in pipeline.sources)


def with_placeholders(pipeline: Pipeline) -> Pipeline:
    """A copy whose Jira sources with typed values carry placeholder references: for the cheap, static validation before any value is stored."""
    copy = Pipeline.from_dict(pipeline.to_dict())
    copy.sources = []
    for s in pipeline.sources:
        if s.type == "jira" and (s.options.get("_email") or s.options.get("_token")):
            options = {k: v for k, v in s.options.items() if not k.startswith("_")}
            options["email_ref"] = options.get("email_ref") or "vault:v_pending"
            options["token_ref"] = options.get("token_ref") or "vault:v_pending"
            copy.sources.append(Source(s.path, s.label, type="jira", options=options))
        else:
            copy.sources.append(s)
    return copy


def apply_typed(pipeline: Pipeline, saved: Pipeline | None = None) -> list[str]:
    """For every Jira source of ``pipeline`` with typed values: check them against Jira, keep them in the vault and point the
    source at ``vault:<id>``. Returns the ids of secrets this call CREATED (so a failed save can remove them)."""
    created: list[str] = []
    vault = vault_mod.default_vault()
    try:
        for source in pipeline.sources:
            if source.type != "jira" or not (source.options.get("_email") or source.options.get("_token")):
                continue
            email, token = source.options.pop("_email", None), source.options.pop("_token", None)
            before = next((s for s in (saved.sources if saved else []) if s.type == "jira" and s.label == source.label), None)
            if before is not None:
                for key in ("email_ref", "token_ref"):
                    source.options.setdefault(key, before.options.get(key, ""))
            check_values(source, email, token)
            for value, key, kind, label in ((email, "email_ref", "jira-email", "Jira email"), (token, "token_ref", "jira-token", "Jira API token")):
                if not value:
                    continue
                ref = source.options.get(key, "")
                if ref.startswith("vault:") and vault.has(ref[len("vault:"):]):
                    vault.replace(ref[len("vault:"):], value)
                else:
                    secret_id = vault.add(kind, f"{label} for {source.label}", value)
                    created.append(secret_id)
                    source.options[key] = f"vault:{secret_id}"
    except Exception:
        rollback(created)
        raise
    return created


def rollback(created: list[str]) -> None:
    for secret_id in created:
        try:
            vault_mod.default_vault().remove(secret_id)
        except vault_mod.VaultError:
            pass
