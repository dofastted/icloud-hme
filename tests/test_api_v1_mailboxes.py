import web_ui
from api_keys import APIKeyStore
from mailbox_service import MailboxService
from shared_mailboxes import SharedMailboxStore


class FakeCache:
    def cache_age_seconds(self, _acc_id):
        return 1


class FakeMail:
    def find_by_recipient(self, alias, limit=20, days=30):
        return [
            {"id": "m1", "subject": "Hello", "from": "sender@example.com", "to": alias, "date": "2026-01-01", "body_preview": "preview"},
            {"id": "m2", "subject": "Older", "from": "sender@example.com", "to": alias, "date": "2025-12-31", "body_preview": "old"},
        ][:limit]

    def fetch_full(self, msg_id):
        return {"id": msg_id.decode(), "subject": "Hello", "from": "sender@example.com", "to": "alias@icloud.com", "date": "2026-01-01", "body": "full body"}

    def disconnect(self):
        pass


class FakeManager:
    def __init__(self):
        self._cache = FakeCache()
        self.accounts = {"acc_1": {"id": "acc_1", "name": "Main", "status": "active", "app_password": "pwd", "icloud_email": "main@icloud.com"}}

    def list_accounts(self):
        return list(self.accounts.values())

    def get_account(self, acc_id):
        return self.accounts.get(acc_id)

    def get_all_aliases(self):
        return [{"hme": "alias@icloud.com", "account_id": "acc_1", "account_name": "Main", "label": "Login", "isActive": True}]

    def get_summary(self):
        return {"account_count": 1, "active_accounts": 1, "error_accounts": 0, "total_aliases": 1, "total_active_aliases": 1}

    def get_mail_client(self, _acc_id):
        return FakeMail()


class NoMailConfigManager(FakeManager):
    def __init__(self):
        super().__init__()
        self.accounts["acc_1"].pop("app_password", None)
        self.accounts["acc_1"].pop("icloud_email", None)

    def get_mail_client(self, _acc_id):
        raise ValueError("未设置 App 专用密码。 请点击下方按钮，输入 @icloud.com 邮箱和应用密码")


def configure_manager(monkeypatch, tmp_path, manager):
    store = SharedMailboxStore(tmp_path / "shared.json")
    api_keys = APIKeyStore(tmp_path / "api_keys.json")
    key = api_keys.create("test")["api_key"]
    latest = tmp_path / "latest.txt"
    latest.write_text("alias@icloud.com\tacc_1\n", encoding="utf-8")
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_shared_store", store)
    monkeypatch.setattr(web_ui, "_mailbox_service", MailboxService(manager, store, latest, tmp_path / "mailbox_index.json"))
    monkeypatch.setattr(web_ui, "_api_keys", api_keys)
    return web_ui.app.test_client(), key, store


def configure(monkeypatch, tmp_path):
    manager = FakeManager()
    store = SharedMailboxStore(tmp_path / "shared.json")
    api_keys = APIKeyStore(tmp_path / "api_keys.json")
    key = api_keys.create("test")["api_key"]
    latest = tmp_path / "latest.txt"
    latest.write_text("alias@icloud.com\tacc_1\n", encoding="utf-8")
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_shared_store", store)
    monkeypatch.setattr(web_ui, "_mailbox_service", MailboxService(manager, store, latest, tmp_path / "mailbox_index.json"))
    monkeypatch.setattr(web_ui, "_api_keys", api_keys)
    return web_ui.app.test_client(), key, store


def test_v1_mailboxes_requires_api_key(monkeypatch, tmp_path):
    client, _key, _store = configure(monkeypatch, tmp_path)

    assert client.get("/api/v1/mailboxes").status_code == 401

    assert client.get("/api/v1/config").status_code == 401
    assert client.get("/api/v1/hme/available").status_code == 401


def test_accounts_do_not_expose_legacy_mail_config(monkeypatch, tmp_path):
    client, key, _store = configure(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {key}"}

    admin = client.get("/api/accounts")
    assert admin.status_code == 200
    admin_account = admin.json["accounts"][0]
    assert "app_password" not in admin_account
    assert "icloud_email" not in admin_account
    assert "has_app_password" not in admin_account

    api = client.get("/api/v1/accounts", headers=headers)
    assert api.status_code == 200
    api_account = api.json["accounts"][0]
    assert "app_password" not in api_account
    assert "icloud_email" not in api_account
    assert "has_app_password" not in api_account


def test_v1_config_exposes_global_key_entry(monkeypatch, tmp_path):
    client, key, _store = configure(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {key}"}

    res = client.get("/api/v1/config", headers=headers)

    assert res.status_code == 200
    data = res.json
    assert data["api"]["base_url"].endswith("/api/v1")
    assert data["api"]["auth"]["type"] == "api_key"
    assert data["api"]["auth"]["key_prefix"] == key[:12]
    assert data["api"]["entrypoints"]["available_hme"] == "/api/v1/hme/available"
    assert data["api"]["entrypoints"]["hme_latest"] == "/api/v1/hme/{alias_email}/latest?force=0"
    assert data["counts"]["available_hme"] == 1


def test_v1_global_available_hme_and_next(monkeypatch, tmp_path):
    client, key, _store = configure(monkeypatch, tmp_path)
    headers = {"X-API-Key": key}

    listed = client.get("/api/v1/hme/available?readable=1", headers=headers)
    assert listed.status_code == 200
    assert listed.json["total"] == 1
    assert listed.json["hme"][0]["hme"] == "alias@icloud.com"
    assert listed.json["hme"][0]["can_read_mail"] is True

    next_item = client.get("/api/v1/hme/available/next?include_latest=1", headers=headers)
    assert next_item.status_code == 200
    assert next_item.json["item"]["alias_email"] == "alias@icloud.com"
    assert next_item.json["item"]["latest_message"]["message_id"] == "m1"

    latest = client.get("/api/v1/hme/alias@icloud.com/latest", headers=headers)
    assert latest.status_code == 200
    assert latest.json["hme"] == "alias@icloud.com"
    assert latest.json["message"]["message_id"] == "m1"


def test_v1_available_hme_does_not_require_legacy_mail_config(monkeypatch, tmp_path):
    client, key, _store = configure_manager(monkeypatch, tmp_path, NoMailConfigManager())
    headers = {"X-API-Key": key}

    listed = client.get("/api/v1/hme/available?readable=1", headers=headers)
    assert listed.status_code == 200
    assert listed.json["total"] == 1
    assert listed.json["hme"][0]["hme"] == "alias@icloud.com"
    assert listed.json["hme"][0]["can_read_mail"] is True

    latest = client.get("/api/v1/hme/alias@icloud.com/latest?force=1", headers=headers)
    assert latest.status_code == 200
    assert latest.json["message"] is None
    assert "App 专用密码" not in latest.get_data(as_text=True)
    assert "应用密码" not in latest.get_data(as_text=True)

    messages = client.get("/api/v1/mailboxes/alias@icloud.com/messages?force=1", headers=headers)
    assert messages.status_code == 400
    assert messages.json["error"] == "邮件读取暂不可用"
    assert "App 专用密码" not in messages.get_data(as_text=True)
    assert "应用密码" not in messages.get_data(as_text=True)

    admin_messages = client.get("/api/mailboxes/alias@icloud.com/messages?force=1")
    assert admin_messages.status_code == 400
    assert admin_messages.json["error"] == "邮件读取暂不可用"
    assert "App 专用密码" not in admin_messages.get_data(as_text=True)
    assert "应用密码" not in admin_messages.get_data(as_text=True)


def test_v1_mailboxes_messages_and_shared_flow(monkeypatch, tmp_path):
    client, key, _store = configure(monkeypatch, tmp_path)
    headers = {"X-API-Key": key}

    listed = client.get("/api/v1/mailboxes", headers=headers)
    assert listed.status_code == 200
    assert listed.json["mailboxes"][0]["alias_email"] == "alias@icloud.com"

    messages = client.get("/api/v1/mailboxes/alias@icloud.com/messages", headers=headers)
    assert messages.status_code == 200
    assert messages.json["count"] == 1
    assert messages.json["messages"][0]["message_id"] == "m1"

    created = client.post("/api/v1/shared-mailboxes", json={"alias_email": "alias@icloud.com"}, headers=headers)
    assert created.status_code == 200
    assert created.json["redemption_code"].startswith("shk_")
    assert created.json["share_key"] == created.json["redemption_code"]
    assert created.json["share_url"].endswith("/shared")
    assert "/shared/" in created.json["legacy_share_url"]
    shared = client.get("/api/v1/shared-mailboxes", headers=headers)
    assert shared.status_code == 200
    assert "share_key" not in shared.get_data(as_text=True)
    share_id = shared.json["shared"][0]["id"]

    revoked = client.post(f"/api/v1/shared-mailboxes/{share_id}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json["ok"] is True


def test_shared_entry_url_uses_configured_public_domain(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARED_PUBLIC_BASE_URL", "https://shared.example.com")
    client, key, _store = configure(monkeypatch, tmp_path)
    headers = {"X-API-Key": key}

    created = client.post("/api/v1/shared-mailboxes", json={"alias_email": "alias@icloud.com"}, headers=headers)

    assert created.status_code == 200
    assert created.json["share_url"] == "https://shared.example.com/shared"
    assert created.json["redemption_code"].startswith("shk_")
