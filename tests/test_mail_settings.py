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
