import json

from mailbox_service import MailboxService

from tests.test_mailbox_service import FakeManager


def _svc(tmp_path, manager=None, latest_lines=""):
    latest = tmp_path / "latest_emails.txt"
    latest.write_text(latest_lines, encoding="utf-8")
    return MailboxService(
        manager or FakeManager(),
        None,
        latest,
        tmp_path / "mailbox_index.json",
    )


def test_local_alias_with_timestamp_column_is_parsed(tmp_path):
    svc = _svc(tmp_path, latest_lines="new@icloud.com\tacc_1\t1787000000000\n")

    items = svc.list_mailboxes()

    assert items[0]["alias_email"] == "new@icloud.com"
    assert items[0]["created_at"] == 1787000000000
    assert items[0]["created_at_estimated"] is False


def test_legacy_two_column_rows_keep_append_order_not_zero(tmp_path):
    """旧格式没有时间列，必须按追加顺序兜底，否则全部并列为 0 无法排序。"""
    svc = _svc(
        tmp_path,
        latest_lines="first@icloud.com\tacc_1\nsecond@icloud.com\tacc_1\nthird@icloud.com\tacc_1\n",
    )

    items = svc.list_mailboxes()

    assert [i["alias_email"] for i in items] == [
        "third@icloud.com",
        "second@icloud.com",
        "first@icloud.com",
    ]
    assert all(i["created_at_estimated"] for i in items)
    assert len({i["created_at"] for i in items}) == 3


def test_newly_created_alias_sorts_above_legacy_rows(tmp_path):
    """新建邮箱带真实时间戳，必须排在无时间的旧记录之前，而不是沉到底部。"""
    svc = _svc(
        tmp_path,
        latest_lines=(
            "old1@icloud.com\tacc_1\n"
            "old2@icloud.com\tacc_1\n"
            "fresh@icloud.com\tacc_1\t1787000000000\n"
        ),
    )

    items = svc.list_mailboxes()

    assert items[0]["alias_email"] == "fresh@icloud.com"


def test_local_estimate_does_not_override_indexed_real_timestamp(tmp_path):
    """本地兜底时间不得覆盖索引里的真实创建时间。"""
    index = tmp_path / "mailbox_index.json"
    index.write_text(
        json.dumps({
            "mailboxes": {
                "alias@icloud.com": {
                    "alias_email": "alias@icloud.com",
                    "account_id": "acc_1",
                    "created_at": 1783932943138,
                    "source": "remote",
                }
            },
            "updated_at": "2026-08-22T02:17:24",
        }),
        encoding="utf-8",
    )
    latest = tmp_path / "latest_emails.txt"
    latest.write_text("alias@icloud.com\tacc_1\n", encoding="utf-8")
    svc = MailboxService(FakeManager(), None, latest, index)

    item = svc.list_mailboxes()[0]

    assert item["created_at"] == 1783932943138
    assert item["created_at_estimated"] is False


def test_sort_modes_respect_real_timestamps(tmp_path):
    svc = _svc(
        tmp_path,
        latest_lines=(
            "b@icloud.com\tacc_1\t1787000000000\n"
            "a@icloud.com\tacc_1\t1786000000000\n"
            "c@icloud.com\tacc_1\t1788000000000\n"
        ),
    )

    newest = [i["alias_email"] for i in svc.list_mailboxes(sort="created_at")]
    oldest = [i["alias_email"] for i in svc.list_mailboxes(sort="created_at_asc")]
    by_alias = [i["alias_email"] for i in svc.list_mailboxes(sort="alias")]

    assert newest == ["c@icloud.com", "b@icloud.com", "a@icloud.com"]
    assert oldest == ["a@icloud.com", "b@icloud.com", "c@icloud.com"]
    assert by_alias == ["a@icloud.com", "b@icloud.com", "c@icloud.com"]


def test_created_alias_is_recorded_with_timestamp(monkeypatch, tmp_path):
    """创建邮箱必须写入第三列时间戳，否则它在列表里无法排序。"""
    import sys
    import types

    import account_manager
    from account_manager import AccountManager

    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(account_manager, "LATEST_EMAILS", tmp_path / "results" / "latest_emails.txt")
    monkeypatch.setattr(account_manager, "CREATE_INTERVAL_SEC", 0)
    monkeypatch.setattr(account_manager.time, "time", lambda: 1787000000.0)

    class FakeHME:
        def __init__(self, *a, **kw):
            pass

        def create_alias(self, label=None, note=None, max_retries=3):
            return {"hme": "written@icloud.com"}

    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=FakeHME))
    mgr = AccountManager()
    account = mgr.add_account("main", "A=1", validate=False)

    mgr.create_aliases_for_account(account["id"], count=1)

    written = (tmp_path / "results" / "latest_emails.txt").read_text(encoding="utf-8").strip()
    parts = written.split("\t")
    assert parts[0] == "written@icloud.com"
    assert parts[2] == "1787000000000"
