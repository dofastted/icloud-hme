import queue

import account_manager
import icloud_hme
import web_ui
from account_manager import AccountManager
from mailbox_service import MailboxService
from shared_mailboxes import SharedMailboxStore
from tests.support import login_admin


class RemoteFailingMailboxManager:
    def __init__(self):
        self.remote_calls = 0
        self.accounts = {
            "acc_1": {"id": "acc_1", "name": "Main", "status": "active", "alias_total": 1, "alias_active": 1}
        }

    def list_accounts(self):
        return list(self.accounts.values())

    def get_account(self, acc_id):
        return self.accounts.get(acc_id)

    def get_all_aliases(self):
        self.remote_calls += 1
        raise AssertionError("refresh=0 mailbox list must not fetch remote aliases")

    def get_summary(self):
        return {"account_count": 1, "active_accounts": 1, "error_accounts": 0, "total_aliases": 1, "total_active_aliases": 1}


class ProbeICloudHME:
    constructed = []

    def __init__(self, *args, **kwargs):
        self.__class__.constructed.append({"args": args, "kwargs": kwargs})

    def validate_session(self):
        raise AssertionError("account routes must queue validation instead of running it inline")

    def get_account_info(self):
        return {"appleId": "main@example.com", "primaryEmail": "main@example.com"}

    def list_aliases(self):
        return []


def _temp_account_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "old_cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path)
    return AccountManager()


def _install_web_dependencies(monkeypatch, tmp_path, manager):
    store = SharedMailboxStore(tmp_path / "shared.json")
    latest = tmp_path / "latest_emails.txt"
    latest.write_text("alias@icloud.com\tacc_1\n", encoding="utf-8")
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_shared_store", store)
    monkeypatch.setattr(web_ui, "_mailbox_service", MailboxService(manager, store, latest, tmp_path / "mailbox_index.json"))
    return login_admin(web_ui.app.test_client())


def _validation_state(payload):
    account = payload.get("account") or {}
    return payload.get("validation_status") or account.get("validation_status") or account.get("status")


def _disable_validation_worker(monkeypatch):
    monkeypatch.setattr(web_ui, "_start_validation_worker", lambda: None)
    if hasattr(web_ui, "_validation_queue"):
        monkeypatch.setattr(web_ui, "_validation_queue", queue.Queue())
    if hasattr(web_ui, "_validation_jobs"):
        lock = getattr(web_ui, "_validation_lock", None)
        if lock:
            with lock:
                web_ui._validation_jobs.clear()
        else:
            web_ui._validation_jobs.clear()



def test_add_account_returns_pending_without_inline_validation(monkeypatch, tmp_path):
    manager = _temp_account_manager(monkeypatch, tmp_path)
    ProbeICloudHME.constructed = []
    monkeypatch.setattr(icloud_hme, "ICloudHME", ProbeICloudHME)
    _disable_validation_worker(monkeypatch)
    client = _install_web_dependencies(monkeypatch, tmp_path, manager)

    response = client.post(
        "/api/accounts/add",
        json={"name": "Main", "cookie_input": "X-APPLE-WEBAUTH-TOKEN=token; dsid=1", "host": "icloud.com"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert ProbeICloudHME.constructed == []
    assert _validation_state(payload) in {"queued", "pending"}


def test_update_account_session_returns_pending_without_inline_validation(monkeypatch, tmp_path):
    manager = _temp_account_manager(monkeypatch, tmp_path)
    manager.accounts["acc_1"] = {
        "id": "acc_1",
        "name": "Main",
        "cookies": {"old": "cookie"},
        "host": "icloud.com",
        "status": "active",
        "alias_total": 0,
        "alias_active": 0,
    }
    manager._save()
    ProbeICloudHME.constructed = []
    monkeypatch.setattr(icloud_hme, "ICloudHME", ProbeICloudHME)
    _disable_validation_worker(monkeypatch)
    client = _install_web_dependencies(monkeypatch, tmp_path, manager)

    response = client.post(
        "/api/accounts/acc_1/session",
        json={"name": "Main", "cookie_input": "X-APPLE-WEBAUTH-TOKEN=new; dsid=2", "host": "icloud.com"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert ProbeICloudHME.constructed == []
    assert _validation_state(payload) in {"queued", "pending"}


def test_mailboxes_refresh_zero_payload_is_marked_local_and_skips_remote(monkeypatch, tmp_path):
    manager = RemoteFailingMailboxManager()
    client = _install_web_dependencies(monkeypatch, tmp_path, manager)

    response = client.get("/api/mailboxes?refresh=0&limit=20")

    assert response.status_code == 200
    payload = response.get_json()
    assert manager.remote_calls == 0
    assert payload["refreshed"] is False
    assert payload["source"] == "local"
    assert payload["mailboxes"][0]["source"] == "local"


def test_logs_history_endpoint_returns_recent_emitted_entries(monkeypatch):
    monkeypatch.setattr(web_ui, "_log_queue", queue.Queue())
    if hasattr(web_ui, "_log_history"):
        web_ui._log_history.clear()
    if hasattr(web_ui, "_log_buffer"):
        web_ui._log_buffer.clear()
    if hasattr(web_ui, "_log_seq"):
        web_ui._log_seq = 0
    client = login_admin(web_ui.app.test_client())

    web_ui._emit_log("info", "first historical line")
    web_ui._emit_log("warn", "second historical line")

    response = client.get("/api/logs?limit=1")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert len(payload["logs"]) == 1
    assert payload["logs"][0]["level"] == "warn"
    assert payload["logs"][0]["msg"] == "second historical line"
    assert "time" in payload["logs"][0]
