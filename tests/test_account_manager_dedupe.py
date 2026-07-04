import sys
import types

import account_manager
from account_manager import AccountManager


class FakeHME:
    def __init__(self, cookies, host="icloud.com", verbose=False):
        self.cookies = cookies

    def validate_session(self):
        return {"ok": True}

    def get_account_info(self):
        return {
            "appleId": "duplicate@example.com",
            "primaryEmail": "duplicate@icloud.com",
        }

    def list_aliases(self):
        return [{"active": True}, {"active": False}]


def test_add_account_updates_existing_identity_without_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=FakeHME))

    mgr = AccountManager()
    first = mgr.add_account("first", "A=1")
    second = mgr.add_account("second", "A=2")
    accounts = mgr.list_accounts()

    assert len(accounts) == 1
    assert second["id"] == first["id"]
    assert accounts[0]["name"] == "first"
    assert accounts[0]["cookies"] == {"A": "2"}
    assert accounts[0]["alias_total"] == 2


def test_add_account_dedupes_same_pending_session(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")

    mgr = AccountManager()
    first = mgr.add_account("first", "A=1", validate=False)
    second = mgr.add_account("second", "A=1", validate=False)
    accounts = mgr.list_accounts()

    assert len(accounts) == 1
    assert second["id"] == first["id"]
    assert accounts[0]["name"] == "first"
    assert accounts[0]["cookies"] == {"A": "1"}


def test_update_account_session_replaces_saved_cookie(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=FakeHME))

    mgr = AccountManager()
    account = mgr.add_account("first", "A=1")
    session = mgr.get_account_session(account["id"])
    updated = mgr.update_account_session(account["id"], "renamed", "B=2")

    assert session["cookie_input"] == "A=1"
    assert updated["id"] == account["id"]
    assert updated["name"] == "renamed"
    assert updated["cookies"] == {"B": "2"}
    assert updated["status"] == "active"
