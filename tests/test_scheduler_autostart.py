import logging
from types import SimpleNamespace

import scheduler
import web_ui
from account_manager import SCHEDULER_ALIAS_LIMIT, account_reached_scheduler_limit, scheduler_eligible_accounts


class FakeManager:
    def __init__(self, accounts, refreshed_counts=None):
        self.accounts = {account["id"]: dict(account) for account in accounts}
        self.refreshed_counts = refreshed_counts or {}
        self.created_for = []

    def list_accounts(self):
        return list(self.accounts.values())

    def get_aliases_for_account(self, acc_id):
        total = self.refreshed_counts.get(acc_id, self.accounts[acc_id].get("alias_total", 0))
        return [{"active": True} for _ in range(total)]

    def update_account(self, acc_id, **kwargs):
        self.accounts[acc_id].update(kwargs)
        return dict(self.accounts[acc_id])

    def create_aliases_for_account(self, acc_id, count=1, label=""):
        self.created_for.append(acc_id)
        account = self.accounts[acc_id]
        account["alias_total"] = account.get("alias_total", 0) + 1
        return [{"ok": True, "email": f"{acc_id}-{account['alias_total']}@icloud.com"}]


def test_scheduler_eligible_accounts_skip_single_account_limit():
    accounts = [
        {"id": "full", "status": "active", "alias_total": SCHEDULER_ALIAS_LIMIT},
        {"id": "open", "status": "active", "alias_total": SCHEDULER_ALIAS_LIMIT - 1},
        {"id": "off", "status": "error", "alias_total": 0},
    ]

    assert account_reached_scheduler_limit(accounts[0]) is True
    assert [account["id"] for account in scheduler_eligible_accounts(accounts)] == ["open"]


def test_run_one_round_skips_account_reaching_limit_after_refresh(monkeypatch):
    monkeypatch.setattr(scheduler.time, "sleep", lambda _seconds: None)
    mgr = FakeManager(
        [{"id": "stale", "name": "Stale", "status": "active", "alias_total": SCHEDULER_ALIAS_LIMIT - 1}],
        refreshed_counts={"stale": SCHEDULER_ALIAS_LIMIT},
    )

    result = scheduler.run_one_round(mgr, logging.getLogger("test-scheduler"))

    assert result.created == []
    assert mgr.created_for == []
    assert mgr.accounts["stale"]["alias_total"] == SCHEDULER_ALIAS_LIMIT


def test_run_one_round_caps_creation_to_remaining_capacity(monkeypatch):
    monkeypatch.setattr(scheduler.time, "sleep", lambda _seconds: None)
    mgr = FakeManager([
        {"id": "open", "name": "Open", "status": "active", "alias_total": SCHEDULER_ALIAS_LIMIT - 2}
    ])

    result = scheduler.run_one_round(mgr, logging.getLogger("test-scheduler"))

    assert len(result.created) == 2
    assert mgr.created_for == ["open", "open"]
    assert mgr.accounts["open"]["alias_total"] == SCHEDULER_ALIAS_LIMIT


def test_web_ui_auto_start_scheduler_env(monkeypatch):
    args = SimpleNamespace(scheduler=False)
    monkeypatch.delenv("AUTO_START_SCHEDULER", raising=False)
    assert web_ui._auto_start_scheduler_requested(args) is False

    monkeypatch.setenv("AUTO_START_SCHEDULER", "1")
    assert web_ui._auto_start_scheduler_requested(args) is True

    monkeypatch.setenv("AUTO_START_SCHEDULER", "false")
    assert web_ui._auto_start_scheduler_requested(SimpleNamespace(scheduler=True)) is True


def test_web_ui_start_scheduler_is_idempotent(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon
            self._alive = False

        def start(self):
            self._alive = True
            started.append(self.target)

        def is_alive(self):
            return self._alive

    monkeypatch.setattr(web_ui.threading, "Thread", FakeThread)
    monkeypatch.setattr(web_ui, "_scheduler_thread", None)
    web_ui._stop_event.set()

    assert web_ui._start_scheduler_thread() is True
    assert web_ui._stop_event.is_set() is False
    assert web_ui._start_scheduler_thread() is False
    assert started == [web_ui._scheduler_loop]
