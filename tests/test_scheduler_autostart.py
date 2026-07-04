import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone

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



def test_run_one_round_trusts_local_count_when_capacity_is_clear(monkeypatch):
    monkeypatch.setattr(scheduler.time, "sleep", lambda _seconds: None)

    class RemoteForbiddenManager(FakeManager):
        def __init__(self):
            super().__init__([
                {"id": "open", "name": "Open", "status": "active", "alias_total": SCHEDULER_ALIAS_LIMIT - 20}
            ])
            self.remote_calls = 0

        def get_aliases_for_account(self, acc_id):
            self.remote_calls += 1
            raise AssertionError(f"unexpected remote alias refresh for {acc_id}")

    mgr = RemoteForbiddenManager()

    result = scheduler.run_one_round(mgr, logging.getLogger("test-scheduler-local-count"))

    assert mgr.remote_calls == 0
    assert result.created


def test_web_scheduler_trusts_local_count_when_capacity_is_clear(monkeypatch):
    class RemoteForbiddenManager(FakeManager):
        def __init__(self):
            super().__init__([
                {"id": "open", "name": "Open", "status": "active", "alias_total": SCHEDULER_ALIAS_LIMIT - 20}
            ])
            self.remote_calls = 0

        def get_aliases_for_account(self, acc_id):
            self.remote_calls += 1
            raise AssertionError(f"unexpected remote alias refresh for {acc_id}")

    mgr = RemoteForbiddenManager()
    monkeypatch.setattr(web_ui, "_account_mgr", mgr)
    account = dict(mgr.accounts["open"])

    refreshed = web_ui._refresh_scheduler_account_count(account)

    assert mgr.remote_calls == 0
    assert refreshed["alias_total"] == SCHEDULER_ALIAS_LIMIT - 20

def test_web_ui_auto_start_scheduler_env(monkeypatch):
    args = SimpleNamespace(scheduler=False)
    monkeypatch.delenv("AUTO_START_SCHEDULER", raising=False)
    assert web_ui._auto_start_scheduler_requested(args) is False

    monkeypatch.setenv("AUTO_START_SCHEDULER", "1")
    assert web_ui._auto_start_scheduler_requested(args) is True

    monkeypatch.setenv("AUTO_START_SCHEDULER", "false")
    assert web_ui._auto_start_scheduler_requested(SimpleNamespace(scheduler=True)) is True


def test_web_scheduler_uses_beijing_timezone_without_double_offset(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            utc_now = datetime(2026, 7, 4, 6, 30, tzinfo=timezone.utc)
            if tz:
                return utc_now.astimezone(tz)
            return utc_now.replace(tzinfo=None)

    monkeypatch.setattr(web_ui, "datetime", FixedDateTime)
    monkeypatch.setattr(web_ui, "_time_offset", 0.0)

    bj_now = web_ui._beijing_now()

    assert bj_now.hour == 14
    assert web_ui._scheduler_window_is_open(bj_now) is True


def test_web_scheduler_next_window_uses_next_beijing_morning():
    bj_now = datetime(2026, 7, 4, 22, 5, tzinfo=web_ui.BEIJING_TZ)

    next_start = web_ui._next_scheduler_window_start(bj_now)

    assert next_start.day == 5
    assert next_start.hour == 7
    assert next_start.minute == 0


def test_web_scheduler_uses_manual_create_path(monkeypatch):
    class ManualCreateManager:
        def __init__(self):
            self.calls = []

        def create_aliases_for_account(self, acc_id, count=1, label=""):
            self.calls.append({"acc_id": acc_id, "count": count, "label": label})
            return [{"ok": True, "email": "scheduled@icloud.com"}]

    manager = ManualCreateManager()
    monkeypatch.setattr(web_ui, "_account_mgr", manager)
    monkeypatch.setattr(
        web_ui,
        "_beijing_now",
        lambda: datetime(2026, 7, 4, 14, 30, tzinfo=web_ui.BEIJING_TZ),
    )

    ok, email = web_ui._create_scheduled_alias("acc_1", "Main")

    assert ok is True
    assert email == "scheduled@icloud.com"
    assert manager.calls == [{"acc_id": "acc_1", "count": 1, "label": "Main 07041430"}]


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


def test_autostart_service_unit_starts_scheduler():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.update({
        "DRY_RUN": "1",
        "PYTHON_BIN": "/usr/bin/python3",
        "HOST": "127.0.0.1",
        "PORT": "6060",
        "SERVICE_NAME": "icloud-hme-test.service",
    })

    result = subprocess.run(
        ["sh", "scripts/install-autostart-service.sh"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    unit = result.stdout
    assert "RequiresMountsFor=" in unit
    assert "Environment=PYTHONPATH=" in unit
    assert "Environment=AUTO_START_SCHEDULER=1" in unit
    assert "ExecStart=/usr/bin/python3 -u " in unit
    assert "web_ui.py --scheduler --no-sync" in unit


def test_autostart_install_restarts_existing_service():
    script = Path("scripts/install-autostart-service.sh").read_text(encoding="utf-8")

    assert 'systemctl enable "$SERVICE_NAME"' in script
    assert 'systemctl restart "$SERVICE_NAME"' in script
