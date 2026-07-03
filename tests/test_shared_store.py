import json

import pytest

from shared_mailboxes import SharedMailboxStore


def test_shared_store_create_verify_revoke_and_no_plaintext(tmp_path):
    path = tmp_path / "shared_mailboxes.json"
    store = SharedMailboxStore(path)

    created = store.create("acc_1", "Alias@iCloud.com")
    raw_key = created["share_key"]

    assert raw_key.startswith("shk_")
    assert created["alias_email"] == "alias@icloud.com"
    assert raw_key not in path.read_text(encoding="utf-8")
    assert created["prefix"] in json.dumps(json.loads(path.read_text(encoding="utf-8")))

    verified = store.verify(raw_key)
    assert verified["id"] == created["id"]
    assert verified["access_count"] == 1
    assert store.verify(raw_key + "bad") is None

    assert store.revoke(created["id"]) is True
    assert store.verify(raw_key) is None


def test_shared_store_duplicate_active_requires_revoke(tmp_path):
    store = SharedMailboxStore(tmp_path / "shared_mailboxes.json")
    first = store.create("acc_1", "a@icloud.com")

    with pytest.raises(ValueError):
        store.create("acc_1", "A@iCloud.com")

    assert store.revoke(first["id"]) is True
    second = store.create("acc_1", "a@icloud.com")
    assert second["id"] != first["id"]


def test_shared_store_list_omits_plaintext(tmp_path):
    store = SharedMailboxStore(tmp_path / "shared_mailboxes.json")
    created = store.create("acc_1", "a@icloud.com")
    listed = store.list()

    assert listed[0]["id"] == created["id"]
    assert "share_key" not in listed[0]
    assert "sha256" not in listed[0]
