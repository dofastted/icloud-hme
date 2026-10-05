import hashlib
import json

import web_ui
from admin_auth import SCRYPT_DKLEN, SCRYPT_N, SCRYPT_P, SCRYPT_R, AdminAuthStore
from api_keys import APIKeyStore
from tests.support import login_admin


def test_anonymous_pages_and_management_apis_stay_closed():
    client = web_ui.app.test_client()

    page = client.get("/")
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "管理员登录" in body
    assert "01-core.js" not in body
    assert "仪表盘" not in body
    other = client.get("/index.html")
    assert "管理员登录" in other.get_data(as_text=True)

    state = client.get("/api/state")
    accounts = client.get("/api/accounts")
    shared = client.get("/api/shared")
    assert state.status_code == accounts.status_code == shared.status_code == 401
    state_body = state.get_json()
    assert state_body == {"ok": False, "error": "未登录"}
    assert "mail_password" not in accounts.get_data(as_text=True)
    assert "accounts" not in state_body


def test_default_login_logout_and_wrong_password(monkeypatch):
    logged = []
    def capture_log(level, msg, tag=""):
        del level, tag
        logged.append(msg)

    monkeypatch.setattr(web_ui, "_emit_log", capture_log)
    client = web_ui.app.test_client()

    bad = client.post("/api/login", json={"username": "admin", "password": "wrong-password"})
    bad_body = bad.get_json()
    assert bad.status_code == 401
    assert bad_body["error"] == "账号或密码错误"
    assert "wrong-password" not in bad.get_data(as_text=True)
    assert client.get("/api/state").status_code == 401
    assert all("wrong-password" not in msg and "123456qwe" not in msg for msg in logged)

    assert client.post("/api/login", json={"username": "admin", "password": 123456}).status_code == 401

    login_admin(client)
    page = client.get("/")
    assert "01-core.js" in page.get_data(as_text=True)
    assert client.get("/api/state").status_code == 200

    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/state").status_code == 401
    assert "管理员登录" in client.get("/").get_data(as_text=True)


def test_existing_credential_file_is_not_reset(tmp_path):
    path = tmp_path / "admin.json"
    store = AdminAuthStore(path)
    assert store.verify("admin", "123456qwe") is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    salt = bytes.fromhex(saved["salt"])
    saved["password_hash"] = hashlib.scrypt(
        b"other-secret", salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
    ).hex()
    path.write_text(json.dumps(saved), encoding="utf-8")
    before = path.read_bytes()

    again = AdminAuthStore(path)
    assert again.verify("admin", "123456qwe") is False
    assert again.verify("admin", "other-secret") is True
    assert path.read_bytes() == before

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert AdminAuthStore(broken).verify("admin", "123456qwe") is False
    assert broken.read_text(encoding="utf-8") == "{"


def test_v1_keeps_api_key_without_admin_session(monkeypatch, tmp_path):
    keys = APIKeyStore(tmp_path / "api_keys.json")
    created = keys.create("remote")
    monkeypatch.setattr(web_ui, "_api_keys", keys)
    client = web_ui.app.test_client()

    denied = client.get("/api/v1/config")
    denied_body = denied.get_json()
    assert denied.status_code == 401
    assert denied_body["error"] == "invalid API key"

    allowed = client.get("/api/v1/config", headers={"X-API-Key": created["api_key"]})
    assert allowed.status_code == 200


def test_public_redeem_stays_open_and_revoke_stays_closed(monkeypatch):
    revoked = []
    monkeypatch.setattr(web_ui._shared_store, "revoke", lambda share_id: revoked.append(share_id) or True)
    client = web_ui.app.test_client()

    assert client.post("/api/shared/share_1/revoke").status_code == 401
    assert revoked == []
    assert client.get("/api/shared/missing-key/latest").status_code != 401
    assert client.post("/api/shared/latest", json={}).status_code != 401
    assert client.post("/api/shared/redeem", json={}).status_code != 401
    assert client.get("/shared").status_code == 200
