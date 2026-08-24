import json

import pytest

from mailbox_service import IMAPNotConfigured, IMAPUnavailable, MailboxNotFound, MailboxService
from shared_mailboxes import SharedMailboxStore


class FakeCache:
    def cache_age_seconds(self, _acc_id):
        return 12


class CachedMessageCache:
    def cache_age_seconds(self, _acc_id):
        return 1

    def get_alias_mail(self, _acc_id, _alias):
        return [
            {"id": "new", "subject": "New", "from": "n@example.com", "to": "alias@icloud.com", "date": "2026-01-03", "body_preview": "new"},
            {"id": "old", "subject": "Old", "from": "o@example.com", "to": "alias@icloud.com", "date": "2026-01-01", "body_preview": "old"},
        ]


class ProviderMessageCache(CachedMessageCache):
    def get_alias_mail(self, _acc_id, alias):
        if alias == "alias@icloud.com":
            return [
                {"id": "claude", "subject": "Claude sign-in", "date": "2026-01-04"},
                {"id": "openai", "body": "OpenAI verification", "date": "2026-01-03"},
            ]
        return []


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


class HeaderMatchedMail(FakeMail):
    def find_by_recipient(self, alias, limit=20, days=30):
        return [
            {
                "id": "m3",
                "subject": "Forwarded",
                "from": "sender@example.com",
                "to": "real@qq.com",
                "recipient_headers": f"real@qq.com\nX-Original-To: {alias}",
                "matched_recipient": alias,
                "date": "2026-01-04",
                "body_preview": "preview",
            }
        ][:limit]

    def fetch_full(self, msg_id):
        return {
            "id": msg_id.decode(),
            "subject": "Forwarded",
            "from": "sender@example.com",
            "to": "real@qq.com",
            "recipient_headers": "real@qq.com\nX-Original-To: alias@icloud.com",
            "date": "2026-01-04",
            "body": "full forwarded body",
        }



class OpenAIVerificationMail(FakeMail):
    def find_by_recipient(self, alias, limit=20, days=30):
        return [
            {"id": "otp", "subject": "Your temporary ChatGPT verification code", "from": "noreply@tm.openai.com", "to": alias, "date": "2026-01-05", "body_preview": ""},
        ][:limit]

    def fetch_full(self, msg_id):
        return {
            "id": msg_id.decode(),
            "subject": "Your temporary ChatGPT verification code",
            "from": "noreply@tm.openai.com",
            "to": "alias@icloud.com",
            "date": "2026-01-05",
            "body": "Your ChatGPT verification code is 654321. This code expires shortly.",
        }


class AuthFailMail(FakeMail):
    def find_by_recipient(self, alias, limit=20, days=30):
        raise RuntimeError("邮件登录失败 — 请检查邮件认证凭据和账号状态")

    def fetch_full(self, msg_id):
        raise RuntimeError("邮件登录失败 — 请检查邮件认证凭据和账号状态")


class FakeManager:
    def __init__(self, mail=None, get_mail_error=None):
        self._cache = FakeCache()
        self.mail = mail or FakeMail()
        self.get_mail_error = get_mail_error
        self.alias_calls = 0
        self.accounts = {
            "acc_1": {"id": "acc_1", "name": "Main", "status": "active", "app_password": "pwd", "icloud_email": "main@icloud.com"}
        }

    def list_accounts(self):
        return list(self.accounts.values())

    def get_account(self, acc_id):
        return self.accounts.get(acc_id)

    def get_all_aliases(self):
        self.alias_calls += 1
        return [
            {"hme": "alias@icloud.com", "account_id": "acc_1", "account_name": "Main", "label": "Login", "isActive": True, "createTimestamp": "2026-01-01"}
        ]

    def get_mail_client(self, _acc_id):
        if self.get_mail_error:
            raise self.get_mail_error
        return self.mail


def latest_file(tmp_path):
    latest = tmp_path / "latest_emails.txt"
    latest.write_text("alias@icloud.com\tacc_1\nlocal@icloud.com\tacc_1\n", encoding="utf-8")
    return latest


def test_mailbox_service_lists_local_cache_without_remote_wait(tmp_path):
    manager = FakeManager()
    latest = latest_file(tmp_path)
    store = SharedMailboxStore(tmp_path / "shared.json")
    store.create("acc_1", "alias@icloud.com")
    svc = MailboxService(manager, store, latest, tmp_path / "mailbox_index.json")

    all_items = svc.list_mailboxes()
    assert manager.alias_calls == 0
    assert sorted(i["alias_email"] for i in all_items) == ["alias@icloud.com", "local@icloud.com"]
    assert next(i for i in all_items if i["alias_email"] == "alias@icloud.com")["shared"]["prefix"]



def test_mailbox_service_marks_refresh_zero_results_as_local_source(tmp_path):
    manager = FakeManager()
    latest = latest_file(tmp_path)
    svc = MailboxService(manager, latest_emails_path=latest, index_path=tmp_path / "mailbox_index.json")

    items = svc.list_mailboxes(refresh=False)

    assert manager.alias_calls == 0
    assert items
    assert {item["source"] for item in items} == {"local"}

def test_mailbox_refresh_persists_remote_index(tmp_path):
    manager = FakeManager()
    index = tmp_path / "mailbox_index.json"
    svc = MailboxService(manager, None, tmp_path / "missing_latest.txt", index)

    items = svc.list_mailboxes(q="login", refresh=True)
    assert manager.alias_calls == 1
    assert items[0]["alias_email"] == "alias@icloud.com"
    assert items[0]["label"] == "Login"

    saved = json.loads(index.read_text(encoding="utf-8"))
    assert "alias@icloud.com" in saved["mailboxes"]

    second_manager = FakeManager()
    second = MailboxService(second_manager, None, tmp_path / "missing_latest.txt", index)
    cached = second.list_mailboxes(q="login")
    assert second_manager.alias_calls == 0
    assert cached[0]["label"] == "Login"


def test_mailbox_service_latest_and_detail(tmp_path):
    svc = MailboxService(FakeManager(), latest_emails_path=latest_file(tmp_path), index_path=tmp_path / "idx.json")

    latest = svc.get_latest_message("alias@icloud.com")
    assert latest["message_id"] == "m2"
    assert latest["body_preview"] == "full body"
    assert latest["body"] == "full body"

    detail = svc.get_message_detail("alias@icloud.com", "m2")
    assert detail["body"] == "full body"



def test_latest_message_exposes_otp_from_full_body(tmp_path):
    svc = MailboxService(FakeManager(mail=OpenAIVerificationMail()), latest_emails_path=latest_file(tmp_path), index_path=tmp_path / "idx_otp.json")

    latest = svc.get_latest_message("alias@icloud.com", force=True)

    assert latest["message_id"] == "otp"
    assert latest["body"]
    assert latest["otp_code"] == "654321"
    assert latest["verification_code"] == "654321"
    assert latest["code"] == "654321"


def test_mailbox_service_detail_accepts_original_recipient_header(tmp_path):
    svc = MailboxService(
        FakeManager(mail=HeaderMatchedMail()),
        latest_emails_path=latest_file(tmp_path),
        index_path=tmp_path / "idx_forwarded.json",
    )

    latest = svc.get_latest_message("alias@icloud.com", force=True)
    assert latest["message_id"] == "m3"

    detail = svc.get_message_detail("alias@icloud.com", "m3")
    assert detail["body"] == "full forwarded body"

def test_mailbox_service_uses_cached_latest_order(tmp_path):
    manager = FakeManager(mail=FakeMail(fail=True))
    manager._cache = CachedMessageCache()
    svc = MailboxService(manager, latest_emails_path=latest_file(tmp_path), index_path=tmp_path / "idx_cache.json")

    latest = svc.get_latest_message("alias@icloud.com")
    assert latest["message_id"] == "new"
    assert manager.mail.fail is True


def test_public_shared_view_uses_share_record_without_mailbox_index(tmp_path):
    manager = FakeManager()
    svc = MailboxService(manager, latest_emails_path=tmp_path / "missing_latest.txt", index_path=tmp_path / "idx.json")

    view = svc.shared_public_view({"account_id": "acc_1", "alias_email": "alias@icloud.com"})
    assert manager.alias_calls == 0
    assert view["mailbox"] == "alias@icloud.com"
    assert view["message"]["body"] == "full body"


def test_mailbox_service_not_found_and_imap_errors(tmp_path):
    latest = latest_file(tmp_path)
    svc = MailboxService(FakeManager(), latest_emails_path=latest, index_path=tmp_path / "idx.json")
    with pytest.raises(MailboxNotFound):
        svc.get_latest_message("missing@icloud.com")

    configured = MailboxService(FakeManager(get_mail_error=ValueError("no app password")), latest_emails_path=latest, index_path=tmp_path / "idx2.json")
    with pytest.raises(IMAPNotConfigured):
        configured.get_latest_message("alias@icloud.com")

    unavailable = MailboxService(FakeManager(mail=FakeMail(fail=True)), latest_emails_path=latest, index_path=tmp_path / "idx3.json")
    with pytest.raises(IMAPUnavailable):
        unavailable.get_latest_message("alias@icloud.com")

    auth_failed = MailboxService(FakeManager(mail=AuthFailMail()), latest_emails_path=latest, index_path=tmp_path / "idx4.json")
    with pytest.raises(IMAPNotConfigured) as excinfo:
        auth_failed.get_latest_message("alias@icloud.com")
    assert str(excinfo.value) == "邮件读取暂不可用"
    assert "邮件登录失败" not in str(excinfo.value)
    assert "邮件认证凭据" not in str(excinfo.value)


def write_index(tmp_path, mailboxes):
    index = tmp_path / "mailbox_index.json"
    index.write_text(
        json.dumps({"mailboxes": mailboxes, "updated_at": "2026-01-01T00:00:00"}, ensure_ascii=False),
        encoding="utf-8",
    )
    latest = tmp_path / "latest_emails.txt"
    latest.write_text("", encoding="utf-8")
    return latest, index


def test_list_mailboxes_sorts_by_created_time(tmp_path):
    latest, index = write_index(tmp_path, {
        "old@icloud.com": {
            "alias_email": "old@icloud.com",
            "account_id": "acc_1",
            "created_at": 1700000000000,
            "is_active": True,
        },
        "new@icloud.com": {
            "alias_email": "new@icloud.com",
            "account_id": "acc_1",
            "created_at": 1783932943138,
            "is_active": True,
        },
        "mid@icloud.com": {
            "alias_email": "mid@icloud.com",
            "account_id": "acc_1",
            "created_at": "2025-06-01T00:00:00",
            "is_active": True,
        },
    })
    svc = MailboxService(FakeManager(), latest_emails_path=latest, index_path=index)

    newest = [item["alias_email"] for item in svc.list_mailboxes()]
    oldest = [item["alias_email"] for item in svc.list_mailboxes(sort="created_at_asc")]
    by_alias = [item["alias_email"] for item in svc.list_mailboxes(sort="alias")]

    assert newest == ["new@icloud.com", "mid@icloud.com", "old@icloud.com"]
    assert oldest == ["old@icloud.com", "mid@icloud.com", "new@icloud.com"]
    assert by_alias == ["mid@icloud.com", "new@icloud.com", "old@icloud.com"]

def test_list_mailboxes_exposes_cached_latest_subject(tmp_path):
    svc = MailboxService(
        FakeManager(),
        latest_emails_path=latest_file(tmp_path),
        index_path=tmp_path / "idx_subject.json",
    )
    items = {item["alias_email"]: item for item in svc.list_mailboxes()}
    assert items["alias@icloud.com"]["latest_subject"] == ""

    svc.account_mgr._cache = CachedMessageCache()
    items = {item["alias_email"]: item for item in svc.list_mailboxes()}
    assert items["alias@icloud.com"]["latest_subject"] == "New"



def test_list_mailboxes_derives_provider_flags_and_filters_from_cache(tmp_path):
    manager = FakeManager()
    manager._cache = ProviderMessageCache()
    svc = MailboxService(manager, latest_emails_path=latest_file(tmp_path), index_path=tmp_path / "idx_provider.json")

    items = {item["alias_email"]: item for item in svc.list_mailboxes()}
    assert items["alias@icloud.com"]["has_claude"] is True
    assert items["alias@icloud.com"]["has_openai"] is True
    assert items["alias@icloud.com"]["is_empty"] is False
    assert items["local@icloud.com"]["is_empty"] is True
    assert [item["alias_email"] for item in svc.list_mailboxes(mail_kind="claude")] == ["alias@icloud.com"]
    assert [item["alias_email"] for item in svc.list_mailboxes(mail_kind="openai")] == ["alias@icloud.com"]
    assert [item["alias_email"] for item in svc.list_mailboxes(mail_kind="empty")] == ["local@icloud.com"]
def test_delete_mailbox_cleans_local_record_without_remote_id(tmp_path):
    latest = latest_file(tmp_path)
    svc = MailboxService(FakeManager(), latest_emails_path=latest, index_path=tmp_path / "idx.json")

    result = svc.delete_mailbox("alias@icloud.com")

    assert result["remote_deleted"] is False
    assert "缺少 Apple 别名标识" in result["warning"]
    assert "alias@icloud.com" not in latest.read_text(encoding="utf-8")
    assert [item["alias_email"] for item in svc.list_mailboxes()] == ["local@icloud.com"]

