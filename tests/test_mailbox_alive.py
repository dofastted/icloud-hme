import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test_mailbox_alive.py"


def load_script():
    spec = importlib.util.spec_from_file_location("test_mailbox_alive_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_mailbox_alive_inactive_missing_unverified():
    mod = load_script()
    local = {"alias_email": "a@icloud.com", "account_id": "acc_1", "is_active": True}

    alive = mod.classify_mailbox(local, {"hme": "a@icloud.com", "isActive": True, "anonymousId": "x1"}, None)
    inactive = mod.classify_mailbox(local, {"hme": "a@icloud.com", "active": False, "anonymousId": "x2"}, None)
    missing = mod.classify_mailbox(local, None, None)
    unverified = mod.classify_mailbox(local, {"hme": "a@icloud.com", "isActive": False}, "NONAUTH")

    assert alive["status"] == "alive"
    assert inactive["status"] == "inactive"
    assert missing["status"] == "missing"
    assert unverified["status"] == "unverified"
    assert "NONAUTH" in unverified["detail"]


def test_classify_account_mailboxes_marks_remote_only_and_unverified():
    mod = load_script()
    local_items = [
        {"alias_email": "keep@icloud.com", "account_id": "acc_1"},
        {"alias_email": "gone@icloud.com", "account_id": "acc_1"},
    ]
    remote = [
        {"hme": "keep@icloud.com", "isActive": True, "anonymousId": "k1"},
        {"hme": "extra@icloud.com", "isActive": True, "anonymousId": "e1"},
    ]

    rows, remote_only = mod.classify_account_mailboxes(local_items, remote, None)
    by_email = {row["email"]: row["status"] for row in rows}
    assert by_email["keep@icloud.com"] == "alive"
    assert by_email["gone@icloud.com"] == "missing"
    assert remote_only[0]["email"] == "extra@icloud.com"

    unverified_rows, unverified_only = mod.classify_account_mailboxes(local_items, remote, "session expired")
    assert {row["status"] for row in unverified_rows} == {"unverified"}
    assert unverified_only == []


def test_exit_code_prefers_dead_over_unverified():
    mod = load_script()
    assert mod._exit_code({"inactive_count": 1, "missing_count": 0, "unverified_count": 3}) == 1
    assert mod._exit_code({"inactive_count": 0, "missing_count": 2, "unverified_count": 0}) == 1
    assert mod._exit_code({"inactive_count": 0, "missing_count": 0, "unverified_count": 1}) == 2
    assert mod._exit_code({"inactive_count": 0, "missing_count": 0, "unverified_count": 0}) == 0


class _FakeMgr:
    def __init__(self):
        import threading
        self.mailbox_groups = {"dead@icloud.com": "grp_unavailable"}
        self._lock = threading.RLock()
        self.saved = 0

    def _save(self):
        self.saved += 1


class _FakeService:
    def __init__(self, tmp_path):
        self.index_path = tmp_path / "mailbox_index.json"
        self.latest_emails_path = tmp_path / "latest_emails.txt"
        self.index_path.write_text(
            '{"mailboxes": {"dead@icloud.com": {"alias_email": "dead@icloud.com"}, "keep@icloud.com": {"alias_email": "keep@icloud.com"}}, "updated_at": "t"}',
            encoding="utf-8",
        )
        self.latest_emails_path.write_text("dead@icloud.com\tacc_1\nkeep@icloud.com\tacc_1\n", encoding="utf-8")


def test_purge_local_mailbox_removes_index_latest_and_group(tmp_path):
    mod = load_script()
    mgr = _FakeMgr()
    service = _FakeService(tmp_path)
    touched = mod.purge_local_mailbox(mgr, service, "dead@icloud.com")
    assert set(touched) == {"index", "latest_emails", "group"}
    data = json.loads(service.index_path.read_text(encoding="utf-8"))
    assert "dead@icloud.com" not in data["mailboxes"]
    assert "keep@icloud.com" in data["mailboxes"]
    assert "dead@icloud.com" not in service.latest_emails_path.read_text(encoding="utf-8")
    assert "dead@icloud.com" not in mgr.mailbox_groups


def test_delete_mailbox_without_anonymous_id_purges_local_only(tmp_path):
    mod = load_script()
    mgr = _FakeMgr()
    service = _FakeService(tmp_path)
    item = mod.delete_mailbox(
        mgr,
        service,
        "acc_1",
        {"email": "dead@icloud.com", "status": "missing", "anonymous_id": ""},
        {},
    )
    assert item["ok"] is True
    assert item["remote"] == "skipped"
    assert "index" in item["local"]


def test_apply_exit_code_uses_delete_failures():
    mod = load_script()
    assert mod._exit_code({"mode": "apply", "delete_failed_count": 2, "inactive_count": 9}) == 1
    assert mod._exit_code({"mode": "apply", "delete_failed_count": 0, "inactive_count": 9}) == 0

