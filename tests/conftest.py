import pytest

import web_ui


@pytest.fixture(autouse=True)
def isolate_admin_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(web_ui._admin_auth, "path", tmp_path / "admin.json")
    monkeypatch.setattr(web_ui, "_admin_secret_path", lambda: tmp_path / "admin_secret.key")
    web_ui.app.secret_key = "test-secret"
