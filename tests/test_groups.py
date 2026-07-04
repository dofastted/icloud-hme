import json
import sys
import types

import account_manager
import web_ui
from account_manager import AccountManager
from mailbox_service import MailboxService
from shared_mailboxes import SharedMailboxStore


class FakeHME:
    def __init__(self, cookies, host="icloud.com", verbose=False):
        self.cookies = cookies

    def validate_session(self):
        return {"ok": True}

    def get_account_info(self):
        marker = next(iter(self.cookies.values()), "main")
        return {
            "appleId": f"user-{marker}@example.com",
            "primaryEmail": f"user-{marker}@icloud.com",
        }

    def list_aliases(self):
        return []


class GroupAwareFakeManager:
    def __init__(self):
        self._cache = None
        self.accounts = [
            {
                "id": "acc_work",
                "name": "Work Account",
                "group_id": "grp_work",
                "group_name": "Work",
                "group_color": "#123456",
            },
            {
                "id": "acc_personal",
                "name": "Personal Account",
                "group_id": "grp_personal",
                "group_name": "Personal",
                "group_color": "#654321",
            },
        ]

    def list_accounts(self):
        return list(self.accounts)

    def get_all_aliases(self):
        return []


def patch_account_files(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(account_manager, "LATEST_EMAILS", tmp_path / "results" / "latest_emails.txt")


def seed_accounts_file(tmp_path, accounts):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps({"accounts": accounts, "updated_at": "2024-01-01T00:00:00"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def default_group(manager):
    groups = manager.list_groups()
    matches = [group for group in groups if group.get("is_default") is True]
    assert len(matches) == 1, groups
    return matches[0]


def group_ids(manager):
    return [group["id"] for group in manager.list_groups()]


def test_legacy_accounts_file_gets_default_group_and_is_persisted(monkeypatch, tmp_path):
    patch_account_files(monkeypatch, tmp_path)
    accounts_path = seed_accounts_file(
        tmp_path,
        {
            "acc_legacy": {
                "id": "acc_legacy",
                "name": "Legacy Account",
                "cookies": {"SESSION": "secret"},
                "status": "active",
                "created_at": "2024-01-01T00:00:00",
            }
        },
    )

    manager = AccountManager()
    default = default_group(manager)

    account = manager.get_account("acc_legacy")
    assert account["group_id"] == default["id"]

    saved = json.loads(accounts_path.read_text(encoding="utf-8"))
    assert saved["accounts"]["acc_legacy"]["group_id"] == default["id"]
    assert any(group.get("is_default") is True for group in saved["groups"].values())


def test_account_manager_group_lifecycle_reorder_move_and_delete_fallback(monkeypatch, tmp_path):
    patch_account_files(monkeypatch, tmp_path)
    seed_accounts_file(
        tmp_path,
        {
            "acc_one": {"id": "acc_one", "name": "One", "cookies": {}, "status": "active"},
            "acc_two": {"id": "acc_two", "name": "Two", "cookies": {}, "status": "active"},
        },
    )
    manager = AccountManager()
    default = default_group(manager)

    vip = manager.add_group(name="VIP", color="#ff0000")
    cold = manager.add_group(name="Cold", color="#00ff00")

    updated = manager.update_group(vip["id"], name="Important", color="#123456")
    assert updated["name"] == "Important"
    assert updated["color"] == "#123456"

    moved = manager.move_accounts_to_group(["acc_one", "acc_two"], vip["id"])
    assert moved == 2
    assert manager.get_account("acc_one")["group_id"] == vip["id"]
    assert manager.get_account("acc_two")["group_id"] == vip["id"]

    manager.reorder_groups([cold["id"], vip["id"]])
    ordered_custom_ids = [gid for gid in group_ids(manager) if gid != default["id"]]
    assert ordered_custom_ids[:2] == [cold["id"], vip["id"]]

    assert manager.delete_group(vip["id"]) is True
    assert manager.get_account("acc_one")["group_id"] == default["id"]
    assert manager.get_account("acc_two")["group_id"] == default["id"]
    assert vip["id"] not in group_ids(manager)


def configure_web_ui(monkeypatch, tmp_path):
    patch_account_files(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=FakeHME))
    manager = AccountManager()
    store = SharedMailboxStore(tmp_path / "shared.json")
    service = MailboxService(
        manager,
        store,
        latest_emails_path=tmp_path / "latest_emails.txt",
        index_path=tmp_path / "mailbox_index.json",
    )
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(web_ui, "_shared_store", store)
    monkeypatch.setattr(web_ui, "_mailbox_service", service)
    return web_ui.app.test_client(), manager


def test_api_groups_crud_accounts_group_fields_and_account_group_updates(monkeypatch, tmp_path):
    client, manager = configure_web_ui(monkeypatch, tmp_path)

    initial = client.get("/api/groups")
    assert initial.status_code == 200
    initial_data = initial.get_json()
    assert initial_data["ok"] is True
    default = next(group for group in initial_data["groups"] if group.get("is_default") is True)

    created = client.post("/api/groups", json={"name": "VIP", "color": "#123456"})
    assert created.status_code == 200
    created_data = created.get_json()
    assert created_data["ok"] is True
    vip_id = created_data["group"]["id"]

    cold = client.post("/api/groups", json={"name": "Cold", "color": "#654321"}).get_json()["group"]

    updated = client.put(f"/api/groups/{vip_id}", json={"name": "Important", "color": "#abcdef"})
    assert updated.status_code == 200
    assert updated.get_json()["group"]["name"] == "Important"

    reordered = client.put("/api/groups/reorder", json={"group_ids": [cold["id"], vip_id]})
    assert reordered.status_code == 200
    assert reordered.get_json()["ok"] is True

    added = client.post(
        "/api/accounts/add",
        json={"name": "Grouped", "cookie_input": "A=1", "group_id": vip_id},
    )
    assert added.status_code == 200
    added_data = added.get_json()
    assert added_data["ok"] is True
    account = added_data["account"]
    assert account["group_id"] == vip_id
    assert account["group_name"] == "Important"
    assert account["group_color"] == "#abcdef"
    assert "cookies" not in account
    assert "mail_password" not in account

    manager.update_account(account["id"], cookies={"SESSION": "secret"}, mail_password="secret", app_password="legacy")
    accounts_data = client.get("/api/accounts").get_json()
    assert [group["id"] for group in accounts_data["groups"]] == group_ids(manager)
    listed = next(item for item in accounts_data["accounts"] if item["id"] == account["id"])
    assert listed["group_id"] == vip_id
    assert listed["group_name"] == "Important"
    assert listed["group_color"] == "#abcdef"
    assert "cookies" not in listed
    assert "mail_password" not in listed
    assert "app_password" not in listed

    edited = client.post(
        f"/api/accounts/{account['id']}/session",
        json={"name": "Regrouped", "cookie_input": "B=2", "host": "icloud.com", "group_id": cold["id"]},
    )
    assert edited.status_code == 200
    assert edited.get_json()["account"]["group_id"] == cold["id"]

    deleted = client.delete(f"/api/groups/{cold['id']}")
    assert deleted.status_code == 200
    assert deleted.get_json()["ok"] is True
    assert manager.get_account(account["id"])["group_id"] == default["id"]


def test_mailbox_service_filters_by_group_and_returns_group_fields(tmp_path):
    index = tmp_path / "mailbox_index.json"
    index.write_text(
        json.dumps(
            {
                "mailboxes": {
                    "work@icloud.com": {
                        "alias_email": "work@icloud.com",
                        "account_id": "acc_work",
                        "label": "Work Alias",
                        "is_active": True,
                        "created_at": "2024-01-02T00:00:00",
                    },
                    "home@icloud.com": {
                        "alias_email": "home@icloud.com",
                        "account_id": "acc_personal",
                        "label": "Home Alias",
                        "is_active": True,
                        "created_at": "2024-01-03T00:00:00",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = MailboxService(
        GroupAwareFakeManager(),
        latest_emails_path=tmp_path / "latest_emails.txt",
        index_path=index,
    )

    mailboxes = service.list_mailboxes(group_id="grp_work")

    assert [item["alias_email"] for item in mailboxes] == ["work@icloud.com"]
    assert mailboxes[0]["group_id"] == "grp_work"
    assert mailboxes[0]["group_name"] == "Work"
    assert mailboxes[0]["group_color"] == "#123456"
