"""MD-115 (FR-S1): consent choice and install_id are stored locally; unanswered counts as "don't share"."""
from __future__ import annotations

import pytest

from mdpipeline.usage import UsageStore


def test_new_store_has_no_answer_and_does_not_share():
    store = UsageStore()
    assert store.consent == "unanswered"
    assert store.sharing is False


@pytest.mark.parametrize("choice,sharing", [("share", True), ("decline", False)])
def test_consent_choice_is_persisted_across_instances(choice, sharing):
    UsageStore().set_consent(choice)
    again = UsageStore()
    assert again.consent == choice
    assert again.sharing is sharing


def test_unknown_consent_value_is_rejected_and_nothing_changes():
    store = UsageStore()
    with pytest.raises(ValueError):
        store.set_consent("maybe")
    assert store.consent == "unanswered"


def test_install_id_is_random_stable_and_local():
    first = UsageStore().install_id
    assert len(first) >= 32
    assert UsageStore().install_id == first
    assert first.lower() == first and first.replace("-", "").isalnum()


def test_reset_install_id_gives_a_new_one():
    store = UsageStore()
    old = store.install_id
    new = store.reset_install_id()
    assert new != old
    assert UsageStore().install_id == new


def test_corrupt_usage_file_falls_back_to_no_sharing(app_data):
    app_data.dir.mkdir(parents=True, exist_ok=True)
    (app_data.dir / "usage.json").write_text("{not json", encoding="utf-8")
    store = UsageStore()
    assert store.sharing is False
    assert store.install_id          # a fresh one is made, nothing crashes
