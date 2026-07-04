import json
import sys
import types

import pytest
import account_manager
import web_ui
from account_manager import AccountManager, BUILTIN_GROUP_IDS, DEFAULT_GROUP_COLOR, DEFAULT_GROUP_ID, UNAVAILABLE_GROUP_ID
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


class MailboxGroupFakeManager:
    def __init__(self):
        self._cache = None
        self.groups = {
            "grp_default": {"id": "grp_default", "name": "可用", "color": "#1f8b4c"},
            "grp_work": {"id": "grp_work", "name": "Work", "color": "#123456"},
            "grp_personal": {"id": "grp_personal", "name": "Personal", "color": "#654321"},
        }
        self.mailbox_groups = {
            "work@icloud.com": "grp_work",
            "home@icloud.com": "grp_personal",
        }
        self.accounts = [
            {"id": "acc_work", "name": "Work Account"},
            {"id": "acc_personal", "name": "Personal Account"},
        ]

    def list_accounts(self):
        return list(self.accounts)

    def get_all_aliases(self):
        return []

    def get_mailbox_group(self, alias_email):
        gid = self.mailbox_groups.get(str(alias_email).lower(), "grp_default")
        return dict(self.groups[gid])


def patch_account_files(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(account_manager, "LATEST_EMAILS", tmp_path / "results" / "latest_emails.txt")


def seed_accounts_file(tmp_path, payload):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def default_group(manager):
    groups = manager.list_groups()
    matches = [group for group in groups if group.get("is_default") is True]
    assert len(matches) == 1, groups
    assert matches[0]["name"] == "可用"
    return matches[0]


def group_ids(manager):
    return [group["id"] for group in manager.list_groups()]


def test_legacy_accounts_file_gets_default_group_and_mailbox_mapping(monkeypatch, tmp_path):
    patch_account_files(monkeypatch, tmp_path)
    accounts_path = seed_accounts_file(
        tmp_path,
        {
            "accounts": {
                "acc_legacy": {
                    "id": "acc_legacy",
                    "name": "Legacy Account",
                    "cookies": {"SESSION": "secret"},
                    "status": "active",
                    "group_id": "grp_wrong_account_group",
                    "created_at": "2024-01-01T00:00:00",
                }
            },
            "groups": {
                "grp_vip": {
                    "id": "grp_vip",
                    "name": "VIP",
                    "color": "#123456",
                    "sort_order": 1,
                    "is_default": False,
                    "created_at": "2024-01-01T00:00:00",
                }
            },
            "mailbox_groups": {
                "VIP@ICLOUD.COM": "grp_vip",
                "missing@icloud.com": "grp_missing",
                "default@icloud.com": DEFAULT_GROUP_ID,
            },
            "updated_at": "2024-01-01T00:00:00",
        },
    )

    manager = AccountManager()
    default = default_group(manager)

    account = manager.get_account("acc_legacy")
    assert "group_id" not in account
    assert manager.get_mailbox_group("vip@icloud.com")["id"] == "grp_vip"
    assert manager.get_mailbox_group("missing@icloud.com")["id"] == default["id"]

    saved = json.loads(accounts_path.read_text(encoding="utf-8"))
    assert "group_id" not in saved["accounts"]["acc_legacy"]
    assert saved["mailbox_groups"] == {"vip@icloud.com": "grp_vip"}
    groups = manager.list_groups()
    assert [group["name"] for group in groups[:3]] == ["可用", "不可用", "废弃"]
    assert all(group.get("is_system") is True for group in groups[:3])


def test_account_manager_group_lifecycle_reorder_mailbox_move_and_delete(monkeypatch, tmp_path):
    patch_account_files(monkeypatch, tmp_path)
    seed_accounts_file(
        tmp_path,
        {
            "accounts": {
                "acc_one": {"id": "acc_one", "name": "One", "cookies": {}, "status": "active"},
                "acc_two": {"id": "acc_two", "name": "Two", "cookies": {}, "status": "active"},
            }
        },
    )
    manager = AccountManager()
    default = default_group(manager)

    vip = manager.add_group(name="VIP", color="#ff0000")
    cold = manager.add_group(name="Cold", color="#00ff00")

    updated = manager.update_group(vip["id"], name="Important", color="not-a-color")
    assert updated["name"] == "Important"
    assert updated["color"] == DEFAULT_GROUP_COLOR

    moved = manager.move_mailboxes_to_group(["One@iCloud.com", "two@icloud.com"], vip["id"])
    assert moved == 2
    assert manager.get_mailbox_group("one@icloud.com")["id"] == vip["id"]
    assert manager.get_mailbox_group("two@icloud.com")["id"] == vip["id"]
    assert manager.get_group(vip["id"])["mailbox_count"] == 2

    manager.reorder_groups([cold["id"], vip["id"]])
    ordered_custom_ids = [gid for gid in group_ids(manager) if gid not in BUILTIN_GROUP_IDS]
    assert ordered_custom_ids[:2] == [cold["id"], vip["id"]]

    with pytest.raises(ValueError, match="内置状态分组不能删除"):
        manager.delete_group(UNAVAILABLE_GROUP_ID)

    with pytest.raises(ValueError, match="内置状态分组不能编辑"):
        manager.update_group(UNAVAILABLE_GROUP_ID, name="Other")

    assert manager.delete_group(vip["id"]) is True
    assert manager.get_mailbox_group("one@icloud.com")["id"] == default["id"]
    assert manager.get_mailbox_group("two@icloud.com")["id"] == default["id"]
    assert vip["id"] not in group_ids(manager)


def configure_web_ui(monkeypatch, tmp_path):
    patch_account_files(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=FakeHME))
    manager = AccountManager()
    manager.accounts["acc_1"] = {"id": "acc_1", "name": "Main", "cookies": {}, "status": "active"}
    manager._save()
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
    return web_ui.app.test_client(), manager, service.index_path


def test_api_groups_crud_mailbox_group_updates_and_account_sanitizing(monkeypatch, tmp_path):
    client, manager, index_path = configure_web_ui(monkeypatch, tmp_path)
    index_path.write_text(
        json.dumps(
            {
                "mailboxes": {
                    "work@icloud.com": {
                        "alias_email": "work@icloud.com",
                        "account_id": "acc_1",
                        "label": "Work Alias",
                        "is_active": True,
                        "created_at": "2024-01-02T00:00:00",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    initial = client.get("/api/groups")
    assert initial.status_code == 200
    assert initial.get_json()["ok"] is True
    default = next(group for group in initial.get_json()["groups"] if group.get("is_default") is True)

    created = client.post("/api/groups", json={"name": "VIP", "color": "#123456"})
    assert created.status_code == 200
    vip_id = created.get_json()["group"]["id"]
    cold = client.post("/api/groups", json={"name": "Cold", "color": "#654321"}).get_json()["group"]

    updated = client.put(f"/api/groups/{vip_id}", json={"name": "Important", "color": "#abcdef"})
    assert updated.status_code == 200
    assert updated.get_json()["group"]["name"] == "Important"

    reordered = client.put("/api/groups/reorder", json={"group_ids": [cold["id"], vip_id]})
    assert reordered.status_code == 200
    assert reordered.get_json()["ok"] is True

    moved = client.post(
        "/api/mailboxes/batch-update-group",
        json={"alias_emails": ["work@icloud.com"], "group_id": vip_id},
    )
    assert moved.status_code == 200
    assert moved.get_json()["moved"] == 1

    listed = client.get(f"/api/mailboxes?group_id={vip_id}").get_json()["mailboxes"]
    assert [item["alias_email"] for item in listed] == ["work@icloud.com"]
    assert listed[0]["group_id"] == vip_id
    assert listed[0]["group_name"] == "Important"
    assert listed[0]["group_color"] == "#abcdef"

    group_counts = client.get("/api/groups").get_json()["groups"]
    assert next(group for group in group_counts if group["id"] == vip_id)["mailbox_count"] == 1

    added = client.post(
        "/api/accounts/add",
        json={"name": "Grouped", "cookie_input": "A=1", "group_id": vip_id},
    )
    assert added.status_code == 200
    account = added.get_json()["account"]
    assert "group_id" not in account
    assert "group_name" not in account
    assert "cookies" not in account
    assert "mail_password" not in account

    deleted = client.delete(f"/api/groups/{vip_id}")
    assert deleted.status_code == 200
    assert deleted.get_json()["ok"] is True
    mailbox = client.get("/api/mailboxes/work@icloud.com").get_json()["mailbox"]
    assert mailbox["group_id"] == default["id"]


def test_mailbox_service_filters_by_mailbox_group_and_returns_group_fields(tmp_path):
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
        MailboxGroupFakeManager(),
        latest_emails_path=tmp_path / "latest_emails.txt",
        index_path=index,
    )

    mailboxes = service.list_mailboxes(group_id="grp_work")

    assert [item["alias_email"] for item in mailboxes] == ["work@icloud.com"]
    assert mailboxes[0]["group_id"] == "grp_work"
    assert mailboxes[0]["group_name"] == "Work"
    assert mailboxes[0]["group_color"] == "#123456"
