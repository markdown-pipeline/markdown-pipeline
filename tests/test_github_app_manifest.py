"""MD-76: the GitHub App manifest the owner registers (MD-77) asks for exactly the permissions D-PT-14 allows."""
import json
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def manifest() -> dict:
    return json.loads((DOCS / "github-app-manifest.json").read_text(encoding="utf-8"))


def test_permissions_are_contents_write_and_metadata_read_only():
    assert manifest()["default_permissions"] == {"contents": "write", "metadata": "read"}


def test_there_is_no_webhook_and_no_event_subscription():
    m = manifest()
    assert not m.get("hook_attributes") and m.get("default_events", []) == []


def test_the_manifest_holds_no_secret_and_no_client_secret():
    text = (DOCS / "github-app-manifest.json").read_text(encoding="utf-8").lower()
    for word in ("client_secret", "private_key", "webhook_secret", "pem"):
        assert word not in text


def test_the_registration_steps_name_every_setting_the_manifest_cannot_carry():
    steps = (DOCS / "github-app-registration.md").read_text(encoding="utf-8")
    for needle in ("Enable Device Flow", "client ID", "Expire user authorization tokens", "Only on this account"):
        assert needle in steps
