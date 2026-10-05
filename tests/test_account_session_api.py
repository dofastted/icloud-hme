import web_ui
from tests.support import login_admin


class FakeSessionManager:
    def __init__(self):
        self.updated = None
        self.account = {
            "id": "acc_1",
            "name": "Main",
            "real_email": "main@example.com",
            "host": "icloud.com",
            "status": "active",
            "cookies": {"A": "1", "B": "2"},
            "alias_total": 2,
            "alias_active": 1,
            "session_version": 1,
        }

    def get_account_session(self, acc_id):
        assert acc_id == "acc_1"
        return {
            "id": "acc_1",
            "name": "Main",
            "host": "icloud.com",
            "status": "active",
            "real_email": "main@example.com",
            "cookie_input": "A=1; B=2",
        }

    def update_account_session(self, acc_id, name, cookie_input, host, validate=True):
        self.updated = {
            "acc_id": acc_id,
            "name": name,
            "cookie_input": cookie_input,
            "host": host,
        }
        self.account.update({
            "id": acc_id,
            "name": name,
            "real_email": "main@example.com",
            "host": host,
            "status": "pending" if not validate else "active",
            "cookies": {"C": "3"},
            "alias_total": 2,
            "alias_active": 1,
            "session_version": self.account.get("session_version", 0) + 1,
        })
        return dict(self.account)

    def get_account(self, acc_id):
        return dict(self.account) if acc_id == "acc_1" else None

    def update_account(self, acc_id, **kwargs):
        if acc_id != "acc_1":
            return None
        self.account.update(kwargs)
        return dict(self.account)


def test_account_session_endpoint_reads_and_updates(monkeypatch):
    manager = FakeSessionManager()
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_start_validation_worker", lambda: None)
    web_ui._validation_jobs.clear()
    client = login_admin(web_ui.app.test_client())

    read_resp = client.get("/api/accounts/acc_1/session")
    assert read_resp.status_code == 200
    assert read_resp.json["account"]["cookie_input"] == "A=1; B=2"

    update_resp = client.post(
        "/api/accounts/acc_1/session",
        json={"name": "Renamed", "host": "icloud.com.cn", "cookie_input": "C=3"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json["account"]["name"] == "Renamed"
    assert "cookies" not in update_resp.json["account"]
    assert manager.updated == {
        "acc_id": "acc_1",
        "name": "Renamed",
        "cookie_input": "C=3",
        "host": "icloud.com.cn",
    }
