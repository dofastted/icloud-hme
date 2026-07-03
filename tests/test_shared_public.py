import web_ui
from mailbox_service import MailboxService
from shared_mailboxes import SharedMailboxStore


class FakeCache:
    def cache_age_seconds(self, _acc_id):
        return 3


class FakeMail:
    def find_by_recipient(self, alias, limit=20, days=30):
        return [{"id": "m1", "subject": "Public", "from": "sender@example.com", "to": alias, "date": "2026-01-01", "body_preview": "preview"}]

    def fetch_full(self, msg_id):
        return {
            "id": msg_id.decode(),
            "subject": "Public",
            "from": "sender@example.com",
            "to": "alias@icloud.com",
            "date": "2026-01-01",
            "body": "safe public body",
        }

    def disconnect(self):
        pass


class FakeManager:
    def __init__(self):
        self._cache = FakeCache()
        self.accounts = {
            "acc_1": {
                "id": "acc_1",
                "name": "Main",
                "real_email": "real@example.com",
                "status": "active",
                "app_password": "secret-app-password",
                "icloud_email": "main@icloud.com",
            }
        }

    def list_accounts(self):
        return list(self.accounts.values())

    def get_account(self, acc_id):
        return self.accounts.get(acc_id)

    def get_all_aliases(self):
        return [{
            "hme": "alias@icloud.com",
            "account_id": "acc_1",
            "account_name": "Main",
            "label": "Login",
            "isActive": True,
            "anonymousId": "anon-secret",
            "forwardToEmail": "real@example.com",
        }]

    def get_mail_client(self, _acc_id):
        return FakeMail()


def configure(monkeypatch, tmp_path):
    manager = FakeManager()
    store = SharedMailboxStore(tmp_path / "shared.json")
    created = store.create("acc_1", "alias@icloud.com")
    raw_key = created["share_key"]
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_shared_store", store)
    monkeypatch.setattr(web_ui, "_mailbox_service", MailboxService(manager, store, tmp_path / "latest.txt"))
    monkeypatch.setattr(web_ui, "_shared_rate_limiter", web_ui._RateLimiter())
    return web_ui.app.test_client(), raw_key, created["id"]


def test_public_shared_latest_is_whitelisted(monkeypatch, tmp_path):
    client, raw_key, _share_id = configure(monkeypatch, tmp_path)

    res = client.get(f"/api/shared/{raw_key}/latest")
    assert res.status_code == 200
    data = res.json
    assert data["ok"] is True
    assert data["mailbox"] == "alias@icloud.com"
    assert data["message"]["body"] == "safe public body"
    assert "to" not in data["message"]

    body = res.get_data(as_text=True)
    for forbidden in ["account_id", "account_name", "real_email", "icloud_email", "anonymousId", "cookies", "app_password", "secret-app-password", "Traceback"]:
        assert forbidden not in body


def test_public_shared_main_entry_redeems_code(monkeypatch, tmp_path):
    client, raw_key, _share_id = configure(monkeypatch, tmp_path)

    page = client.get("/shared")
    assert page.status_code == 200
    page_body = page.get_data(as_text=True)
    assert "共享邮箱主入口" in page_body
    assert "提取邮箱" in page_body
    assert "刷新邮件" in page_body
    assert "redeemBtn.disabled = false" in page_body

    redeemed = client.post("/api/shared/latest", json={"redemption_code": raw_key})
    assert redeemed.status_code == 200
    assert redeemed.json["mailbox"] == "alias@icloud.com"
    assert redeemed.json["message"]["body"] == "safe public body"

    refreshed = client.post("/api/shared/latest", json={"redemption_code": raw_key, "force": True})
    assert refreshed.status_code == 200
    assert refreshed.json["mailbox"] == "alias@icloud.com"


def test_public_shared_revoked_and_invalid_are_same_404(monkeypatch, tmp_path):
    client, raw_key, share_id = configure(monkeypatch, tmp_path)
    invalid = client.get("/api/shared/shk_invalid/latest")
    web_ui._shared_store.revoke(share_id)
    revoked = client.get(f"/api/shared/{raw_key}/latest")

    assert invalid.status_code == 404
    assert revoked.status_code == 404
    assert invalid.get_data(as_text=True) == revoked.get_data(as_text=True)


def test_public_shared_rate_limit(monkeypatch, tmp_path):
    client, raw_key, _share_id = configure(monkeypatch, tmp_path)

    statuses = [client.get(f"/api/shared/{raw_key}/latest").status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


def test_shared_html_page_200_and_404(monkeypatch, tmp_path):
    client, raw_key, share_id = configure(monkeypatch, tmp_path)

    ok = client.get(f"/shared/{raw_key}")
    assert ok.status_code == 200
    assert "SHARED MAILBOX" in ok.get_data(as_text=True)

    web_ui._shared_store.revoke(share_id)
    missing = client.get(f"/shared/{raw_key}")
    assert missing.status_code == 404
    assert "链接不存在或已失效" in missing.get_data(as_text=True)
