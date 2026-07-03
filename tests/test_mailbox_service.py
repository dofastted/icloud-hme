import pytest

from mailbox_service import IMAPNotConfigured, IMAPUnavailable, MailboxNotFound, MailboxService
from shared_mailboxes import SharedMailboxStore


class FakeCache:
    def cache_age_seconds(self, _acc_id):
        return 12


class FakeMail:
    def __init__(self, fail=False):
        self.fail = fail

    def find_by_recipient(self, alias, limit=20, days=30):
        if self.fail:
            raise RuntimeError("imap down")
        return [
            {"id": "m2", "subject": "Second", "from": "b@example.com", "to": alias, "date": "2026-01-02", "body_preview": "two"},
            {"id": "m1", "subject": "First", "from": "a@example.com", "to": alias, "date": "2026-01-01", "body_preview": "one"},
        ][:limit]

    def fetch_full(self, msg_id):
        return {"id": msg_id.decode(), "subject": "Second", "from": "b@example.com", "to": "alias@icloud.com", "date": "2026-01-02", "body": "full body"}

    def disconnect(self):
        pass


class FakeManager:
    def __init__(self, mail=None, get_mail_error=None):
        self._cache = FakeCache()
        self.mail = mail or FakeMail()
        self.get_mail_error = get_mail_error
        self.accounts = {
            "acc_1": {"id": "acc_1", "name": "Main", "status": "active", "app_password": "pwd", "icloud_email": "main@icloud.com"}
        }

    def list_accounts(self):
        return list(self.accounts.values())

    def get_account(self, acc_id):
        return self.accounts.get(acc_id)

    def get_all_aliases(self):
        return [
            {"hme": "alias@icloud.com", "account_id": "acc_1", "account_name": "Main", "label": "Login", "isActive": True, "createTimestamp": "2026-01-01"}
        ]

    def get_mail_client(self, _acc_id):
        if self.get_mail_error:
            raise self.get_mail_error
        return self.mail


def test_mailbox_service_lists_searches_and_marks_shared(tmp_path):
    latest = tmp_path / "latest_emails.txt"
    latest.write_text("alias@icloud.com\tacc_1\nlocal@icloud.com\tacc_1\n", encoding="utf-8")
    store = SharedMailboxStore(tmp_path / "shared.json")
    store.create("acc_1", "alias@icloud.com")
    svc = MailboxService(FakeManager(), store, latest)

    items = svc.list_mailboxes(q="login")
    assert len(items) == 1
    assert items[0]["alias_email"] == "alias@icloud.com"
    assert items[0]["shared"]["prefix"]

    all_items = svc.list_mailboxes()
    assert sorted(i["alias_email"] for i in all_items) == ["alias@icloud.com", "local@icloud.com"]


def test_mailbox_service_latest_and_detail():
    svc = MailboxService(FakeManager())

    latest = svc.get_latest_message("alias@icloud.com")
    assert latest["message_id"] == "m2"
    assert latest["body_preview"] == "two"

    detail = svc.get_message_detail("alias@icloud.com", "m2")
    assert detail["body"] == "full body"


def test_mailbox_service_not_found_and_imap_errors():
    svc = MailboxService(FakeManager())
    with pytest.raises(MailboxNotFound):
        svc.get_latest_message("missing@icloud.com")

    configured = MailboxService(FakeManager(get_mail_error=ValueError("no app password")))
    with pytest.raises(IMAPNotConfigured):
        configured.get_latest_message("alias@icloud.com")

    unavailable = MailboxService(FakeManager(mail=FakeMail(fail=True)))
    with pytest.raises(IMAPUnavailable):
        unavailable.get_latest_message("alias@icloud.com")
