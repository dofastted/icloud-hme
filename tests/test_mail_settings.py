import json

import account_manager
from account_manager import infer_mail_host, account_has_mail_config, account_mail_email, account_mail_host
from account_manager import AccountManager
from api_keys import APIKeyStore
import imaplib

from icloud_mail import ICloudMail
import web_ui


class FakeMailSettingsManager:
    def __init__(self):
        self.accounts = {
            "acc_1": {
                "id": "acc_1",
                "name": "Main",
                "status": "active",
                "real_email": "user@qq.com",
                "cookies": {"ok": "1"},
            }
        }
        self.saved = None

    def list_accounts(self):
        return list(self.accounts.values())

    def set_mail_settings(self, acc_id, email, password, host="", port=993):
        account = self.accounts[acc_id]
        account.update({
            "mail_email": email,
            "mail_password": password,
            "mail_host": host or infer_mail_host(email),
            "mail_port": int(port),
        })
        self.saved = dict(account)
        return account

    def test_mail_connection(self, acc_id):
        account = self.accounts[acc_id]
        return {
            "ok": True,
            "email": account["mail_email"],
            "server": account["mail_host"],
            "port": account["mail_port"],
            "inbox_count": 1,
        }

    def test_mail_read(self, acc_id, alias_email="", limit=5, days=7):
        account = self.accounts[acc_id]
        return {
            "ok": True,
            "email": account["mail_email"],
            "server": account["mail_host"],
            "port": account["mail_port"],
            "inbox_count": 3,
            "recent_count": 1,
            "alias": alias_email,
            "days": days,
            "messages": [{"subject": "Hello", "from": "a@example.com", "to": alias_email or account["mail_email"]}],
        }


def test_infer_mail_host_for_forwarding_mailboxes():
    assert infer_mail_host("user@qq.com") == "imap.qq.com"
    assert infer_mail_host("user@163.com") == "imap.163.com"
    assert infer_mail_host("user@icloud.com") == "imap.mail.me.com"
    assert infer_mail_host("user@example.invalid") == ""

def test_mail_connection_reports_sanitized_auth_failure(monkeypatch):
    class AuthFailIMAP:
        def __init__(self, *_args, **_kwargs):
            pass

        def login(self, *_args):
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] raw provider detail")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", AuthFailIMAP)

    result = ICloudMail("user@qq.com", "bad", server="imap.qq.com").test_connection()

    assert result == {"ok": False, "error": "邮件登录认证失败，请更新邮件登录配置"}
    assert "raw provider detail" not in result["error"]


def test_mail_connection_sends_imap_id_before_select(monkeypatch):
    class IDRequiredIMAP:
        capabilities = (b"IMAP4rev1", b"ID")

        def __init__(self, *_args, **_kwargs):
            self.state = "AUTH"
            self.id_sent = False

        def login(self, *_args):
            return "OK", [b"logged in"]

        def _simple_command(self, command, payload):
            assert command == "ID"
            assert "iCloud HME" in payload
            self.id_sent = True
            return "OK", [b"ID completed"]

        def select(self, mailbox, readonly=False):
            if mailbox != "INBOX" or not readonly:
                return "NO", [b"bad mailbox"]
            if not self.id_sent:
                return "NO", [b"EXAMINE Unsafe Login"]
            self.state = "SELECTED"
            return "OK", [b"7"]

        def logout(self):
            pass

    monkeypatch.setattr(imaplib, "IMAP4_SSL", IDRequiredIMAP)

    result = ICloudMail("user@163.com", "auth-code", server="imap.163.com").test_connection()

    assert result["ok"] is True
    assert result["inbox_count"] == 7


def test_find_by_recipient_reads_junk_mailbox(monkeypatch):
    class JunkIMAP:
        capabilities = (b"IMAP4rev1",)

        def __init__(self, *_args, **_kwargs):
            self.state = "AUTH"
            self.mailbox = ""

        def login(self, *_args):
            return "OK", [b"logged in"]

        def list(self):
            return "OK", [
                b'() "/" "INBOX"',
                b'(\\Junk) "/" "&V4NXPpCuTvY-"',
            ]

        def select(self, mailbox, readonly=False):
            self.state = "SELECTED"
            self.mailbox = mailbox
            return "OK", [b"0"]

        def uid(self, command, _charset, criteria):
            if command == "SEARCH":
                return ("OK", [b"7"]) if self.mailbox == "&V4NXPpCuTvY-" else ("OK", [b""])
            if command == "FETCH":
                header = (
                    b"From: sender@example.com\r\n"
                    b"To: alias@icloud.com\r\n"
                    b"Subject: Junk hit\r\n"
                    b"Date: Sat, 04 Jul 2026 12:00:00 +0000\r\n"
                    + b"X-Pad: " + b"x" * 160 + b"\r\n"
                )
                return "OK", [(b"7 (BODY[HEADER] {200}", header)]
            return "NO", []

        def logout(self):
            pass

    monkeypatch.setattr(imaplib, "IMAP4_SSL", JunkIMAP)

    messages = ICloudMail("user@163.com", "auth-code", server="imap.163.com").find_by_recipient("alias@icloud.com")

    assert len(messages) == 1
    assert messages[0]["mailbox"] == "&V4NXPpCuTvY-"
    assert messages[0]["id"] == "&V4NXPpCuTvY-:7"
    assert messages[0]["subject"] == "Junk hit"


def test_account_mail_config_prefers_forwarding_mailbox():
    account = {"real_email": "user@qq.com", "mail_password": "secret"}

    assert account_mail_email(account) == "user@qq.com"
    assert account_mail_host(account) == "imap.qq.com"
    assert account_has_mail_config(account) is True


def test_get_mail_client_uses_forwarding_mailbox_host():
    manager = AccountManager()
    manager.accounts = {
        "acc_1": {
            "id": "acc_1",
            "real_email": "user@qq.com",
            "icloud_email": "user@icloud.com",
            "mail_password": "secret",
        }
    }

    mail = manager.get_mail_client("acc_1")

    assert mail.apple_id == "user@qq.com"
    assert mail.server == "imap.qq.com"
    assert mail.port == 993


def test_admin_mail_settings_saves_generic_login_without_exposing_secret(monkeypatch):
    manager = FakeMailSettingsManager()
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    client = web_ui.app.test_client()

    res = client.post(
        "/api/accounts/acc_1/mail-settings",
        json={"email": "user@qq.com", "password": "auth-code", "port": 993},
    )

    assert res.status_code == 200
    body = res.json
    assert body["ok"] is True
    assert body["mail"]["server"] == "imap.qq.com"
    account = body["account"]
    assert account["has_mail_config"] is True
    assert account["mail_email"] == "user@qq.com"
    assert account["mail_host"] == "imap.qq.com"
    assert "mail_password" not in account
    assert "app_password" not in account
    assert manager.saved["mail_password"] == "auth-code"


def test_admin_mail_settings_test_endpoint_reads_saved_imap(monkeypatch):
    manager = FakeMailSettingsManager()
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    client = web_ui.app.test_client()

    client.post(
        "/api/accounts/acc_1/mail-settings",
        json={"email": "user@qq.com", "password": "auth-code", "port": 993},
    )
    res = client.post(
        "/api/accounts/acc_1/mail-settings/test",
        json={"alias": "alias@icloud.com", "limit": 5, "days": 30},
    )

    assert res.status_code == 200
    body = res.json
    assert body["ok"] is True
    assert body["mail"]["server"] == "imap.qq.com"
    assert body["mail"]["alias"] == "alias@icloud.com"
    assert body["mail"]["recent_count"] == 1


def test_check_inbox_reports_imap_read_error_without_cache(monkeypatch):
    class EmptyCache:
        def get_inbox(self, _acc_id):
            return []

        def cache_age_seconds(self, _acc_id):
            return 0

        def set_inbox(self, _acc_id, _emails):
            raise AssertionError("should not cache failed reads")

    class FailingMail:
        def check_inbox(self, *_args, **_kwargs):
            raise RuntimeError("search failed")

        def disconnect(self):
            pass

    manager = AccountManager()
    manager._cache = EmptyCache()
    monkeypatch.setattr(manager, "get_mail_client", lambda _acc_id: FailingMail())

    try:
        manager.check_inbox("acc_fail", force=True)
        raise AssertionError("check_inbox should raise on failed IMAP read")
    except RuntimeError as exc:
        assert "search failed" in str(exc)


def test_v1_mail_settings_requires_api_key_and_handles_bad_request(monkeypatch, tmp_path):
    manager = FakeMailSettingsManager()
    keys = APIKeyStore(tmp_path / "api_keys.json")
    key = keys.create("test")["api_key"]
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_api_keys", keys)
    client = web_ui.app.test_client()

    assert client.post("/api/v1/accounts/acc_1/mail-settings", json={}).status_code == 401

    bad = client.post(
        "/api/v1/accounts/missing/mail-settings",
        headers={"Authorization": f"Bearer {key}"},
        json={"email": "user@qq.com", "password": "auth-code"},
    )

    assert bad.status_code == 400
    assert bad.json["ok"] is False


def patch_account_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(account_manager, "LATEST_EMAILS", tmp_path / "results" / "latest_emails.txt")


def test_account_manager_imap_config_binds_account_without_exposing_password(monkeypatch, tmp_path):
    patch_account_storage(monkeypatch, tmp_path)
    manager = AccountManager()
    manager.accounts["acc_1"] = {"id": "acc_1", "name": "Main", "cookies": {}, "status": "active"}
    manager._save()

    config = manager.add_imap_config("QQ 收件箱", "user@qq.com", "auth-code", "", 993)
    account = manager.set_mail_settings("acc_1", "", "", imap_config_id=config["id"])
    settings = manager.get_account_mail_settings("acc_1", include_password=True)
    mail = manager.get_mail_client("acc_1")

    assert account["imap_config_id"] == config["id"]
    assert settings["mail_email"] == "user@qq.com"
    assert settings["mail_host"] == "imap.qq.com"
    assert settings["mail_password"] == "auth-code"
    assert mail.apple_id == "user@qq.com"
    assert mail.server == "imap.qq.com"
    public = manager.list_imap_configs()[0]
    assert public["has_password"] is True
    assert "password" not in public
    saved = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert saved["imap_configs"][config["id"]]["password"] == "auth-code"


def test_account_manager_update_imap_config_keeps_password_when_blank(monkeypatch, tmp_path):
    patch_account_storage(monkeypatch, tmp_path)
    manager = AccountManager()
    config = manager.add_imap_config("Old", "user@163.com", "secret", "imap.163.com", 993)

    updated = manager.update_imap_config(config["id"], "New", "user@163.com", "", "imap.163.com", 993)

    assert updated["name"] == "New"
    assert manager.imap_configs[config["id"]]["password"] == "secret"


def test_imap_config_api_creates_sanitized_config_and_account_can_select(monkeypatch, tmp_path):
    patch_account_storage(monkeypatch, tmp_path)
    manager = AccountManager()
    manager.accounts["acc_1"] = {"id": "acc_1", "name": "Main", "cookies": {}, "status": "active"}
    manager._save()

    def fake_test_mail_connection(acc_id):
        settings = manager.get_account_mail_settings(acc_id)
        return {
            "ok": True,
            "email": settings["mail_email"],
            "server": settings["mail_host"],
            "port": settings["mail_port"],
            "inbox_count": 2,
        }

    monkeypatch.setattr(manager, "test_mail_connection", fake_test_mail_connection)
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    client = web_ui.app.test_client()

    created = client.post(
        "/api/imap-configs",
        json={"name": "QQ 收件箱", "email": "user@qq.com", "password": "auth-code", "port": 993},
    )
    assert created.status_code == 200
    config = created.json["config"]
    assert config["host"] == "imap.qq.com"
    assert config["has_password"] is True
    assert "password" not in config

    selected = client.post(
        "/api/accounts/acc_1/mail-settings",
        json={"imap_config_id": config["id"]},
    )
    assert selected.status_code == 200
    body = selected.json
    assert body["ok"] is True
    assert body["account"]["imap_config_id"] == config["id"]
    assert body["account"]["imap_config_name"] == "QQ 收件箱"
    assert body["account"]["mail_email"] == "user@qq.com"
    assert body["account"]["mail_host"] == "imap.qq.com"
    assert body["mail"]["server"] == "imap.qq.com"

    listed = client.get("/api/accounts").json
    assert listed["imap_configs"][0]["id"] == config["id"]
    assert "password" not in listed["imap_configs"][0]
