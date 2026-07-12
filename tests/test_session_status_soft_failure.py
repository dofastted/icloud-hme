import sys
import types

import account_manager
from account_manager import AccountManager, is_hard_session_error


class SoftFailHME:
    def __init__(self, cookies, host="icloud.com", verbose=False):
        self.cookies = cookies

    def validate_session(self):
        raise RuntimeError("连接失败: HTTPSConnectionPool timed out")

    def get_account_info(self):
        raise AssertionError("should not be called after validate failure")

    def list_aliases(self):
        return []


class HardFailHME:
    def __init__(self, cookies, host="icloud.com", verbose=False):
        self.cookies = cookies

    def validate_session(self):
        raise RuntimeError(
            'HTTP 421: {"success":false,"requestInfo":[{"country":"US"}],'
            '"configBag":{"urls":{"accountLoginUI":"https://idmsa.apple.com"}}}'
        )

    def get_account_info(self):
        raise AssertionError("should not be called after validate failure")

    def list_aliases(self):
        return []


class OkHME:
    def __init__(self, cookies, host="icloud.com", verbose=False):
        self.cookies = cookies

    def validate_session(self):
        return {"ok": True}

    def get_account_info(self):
        return {
            "appleId": "same@example.com",
            "primaryEmail": "same@icloud.com",
        }

    def list_aliases(self):
        return [{"active": True}]


def _mgr(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    return AccountManager()


def test_is_hard_session_error_classifies_auth_and_network():
    assert is_hard_session_error('HTTP 421: {"accountLoginUI":"..."}') is True
    assert is_hard_session_error("HTTP 401 unauthorized") is True
    assert is_hard_session_error("连接失败: timed out") is False
    assert is_hard_session_error("HTTPSConnectionPool Max retries exceeded") is False
    assert is_hard_session_error("该会话属于已存在账号: other") is True


def test_active_account_soft_failure_keeps_active(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    account = mgr.add_account("main", "A=1", validate=False)
    mgr.update_account(account["id"], status="active", validation_status="ok", last_error=None)
    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=SoftFailHME))

    updated = mgr.validate_account(account["id"], reason="health")

    assert updated["status"] == "active"
    assert updated["validation_status"] == "degraded"
    assert "连接失败" in (updated.get("last_error") or "")


def test_active_account_hard_failure_marks_error(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    account = mgr.add_account("main", "A=1", validate=False)
    mgr.update_account(account["id"], status="active", validation_status="ok", last_error=None)
    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=HardFailHME))

    updated = mgr.validate_account(account["id"], reason="health")

    assert updated["status"] == "error"
    assert updated["validation_status"] == "error"
    assert "421" in (updated.get("last_error") or "")


def test_health_validation_skips_identity_duplicate_demotion(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=OkHME))

    first = mgr.add_account("first", "A=1", validate=False)
    second = mgr.add_account("second", "B=2", validate=False)
    # Simulate two local rows that map to the same Apple identity after refresh.
    mgr.update_account(
        first["id"],
        status="active",
        real_email="same@example.com",
        icloud_email="same@icloud.com",
        last_error=None,
    )
    mgr.update_account(
        second["id"],
        status="active",
        real_email="same@example.com",
        icloud_email="same@icloud.com",
        last_error=None,
    )

    updated = mgr.validate_account(first["id"], reason="health")
    assert updated["status"] == "active"
    assert updated["validation_status"] == "ok"
    assert not (updated.get("last_error") or "").startswith("该会话属于已存在账号")

    # Manual/add-style validation still enforces identity uniqueness.
    demoted = mgr.validate_account(first["id"], reason="manual")
    assert demoted["status"] == "error"
    assert "该会话属于已存在账号" in (demoted.get("last_error") or "")
