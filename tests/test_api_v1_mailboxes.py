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

    def get_mail_client(self, _acc_id):
        return FakeMail()


def configure(monkeypatch, tmp_path):
    manager = FakeManager()
    store = SharedMailboxStore(tmp_path / "shared.json")
    api_keys = APIKeyStore(tmp_path / "api_keys.json")
    key = api_keys.create("test")["api_key"]
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_shared_store", store)
    monkeypatch.setattr(web_ui, "_mailbox_service", MailboxService(manager, store, tmp_path / "latest.txt"))
    monkeypatch.setattr(web_ui, "_api_keys", api_keys)
    return web_ui.app.test_client(), key, store


def test_v1_mailboxes_requires_api_key(monkeypatch, tmp_path):
    client, _key, _store = configure(monkeypatch, tmp_path)

    assert client.get("/api/v1/mailboxes").status_code == 401


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
    assert created.json["share_key"].startswith("shk_")
    assert "/shared/" in created.json["share_url"]

    shared = client.get("/api/v1/shared-mailboxes", headers=headers)
    assert shared.status_code == 200
    assert "share_key" not in shared.get_data(as_text=True)
    share_id = shared.json["shared"][0]["id"]

    revoked = client.post(f"/api/v1/shared-mailboxes/{share_id}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json["ok"] is True
