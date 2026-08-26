import json
from datetime import datetime, timedelta
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import types

import account_manager
import web_ui
from account_manager import AccountManager


def _isolated_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(account_manager, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(account_manager, "OLD_COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(account_manager, "RESULTS_DIR", tmp_path / "results")
    return AccountManager()


# ---- 问题 1：账号被误判 error 后无法恢复 ----


def test_error_account_does_not_block_same_identity_recovery(monkeypatch, tmp_path):
    """两份 cookie 指向同一 Apple 身份时，已 error 的一方不该阻止另一方。"""
    mgr = _isolated_manager(monkeypatch, tmp_path)
    first = mgr.add_account("first", "A=1", validate=False)
    second = mgr.add_account("second", "B=2", validate=False)
    for acc_id, status in ((first["id"], "error"), (second["id"], "active")):
        mgr.update_account(acc_id, status=status, real_email="same@example.com")

    # 对 active 的一方判重时，error 的旧账号不构成阻塞。
    assert mgr._find_account_by_identity(mgr.accounts[second["id"]]) is not None
    assert mgr._find_blocking_duplicate(mgr.accounts[second["id"]]) is None
    # 反之，仍 active 的一方依旧构成阻塞。
    assert mgr._find_blocking_duplicate(mgr.accounts[first["id"]]) is not None


def test_update_session_allowed_when_duplicate_is_error(monkeypatch, tmp_path):
    mgr = _isolated_manager(monkeypatch, tmp_path)
    stale = mgr.add_account("stale", "A=1", validate=False)
    target = mgr.add_account("target", "B=2", validate=False)
    mgr.update_account(stale["id"], status="error", real_email="same@example.com")
    mgr.update_account(target["id"], status="error", real_email="same@example.com")

    updated = mgr.update_account_session(target["id"], "target", "C=3", validate=False)

    assert updated["cookies"] == {"C": "3"}


def test_health_loop_skips_error_accounts(monkeypatch):
    """健康检查只探测 active 账号，不触碰已标记 error 的账号。"""
    queued = []

    class OneShotEvent:
        def __init__(self):
            self._calls = 0

        def wait(self, _timeout=None):
            self._calls += 1
            return self._calls > 1  # 首次放行，第二次终止循环

        def is_set(self):
            return self._calls > 1

    class Manager:
        def list_accounts(self):
            return [
                {"id": "a1", "name": "Active", "status": "active"},
                {"id": "a2", "name": "Errored", "status": "error"},
                {"id": "a3", "name": "Pending", "status": "pending"},
            ]

    monkeypatch.setattr(web_ui, "_account_mgr", Manager())
    monkeypatch.setattr(web_ui, "_health_stop_event", OneShotEvent())
    monkeypatch.setattr(web_ui, "_queue_account_validation", lambda acc_id, reason: queued.append((acc_id, reason)) or True)
    monkeypatch.setenv("HME_HEALTH_FIRST_DELAY_SEC", "0")

    web_ui._health_loop()

    assert [acc for acc, _ in queued] == ["a1"]


def test_health_loop_uses_its_own_stop_event(monkeypatch):
    """停止调度器不得连带杀死健康检查线程。"""
    web_ui._stop_event.set()
    try:
        assert web_ui._health_stop_event.is_set() is False
    finally:
        web_ui._stop_event.clear()


def test_scheduler_reports_when_no_account_is_schedulable(monkeypatch):
    logs = []
    states = []

    class Manager:
        def list_accounts(self):
            return [
                {"id": "a1", "name": "Errored", "status": "error", "alias_total": 10},
                {"id": "a2", "name": "Full", "status": "active", "alias_total": account_manager.SCHEDULER_ALIAS_LIMIT},
            ]

    class StopAfterFirstWait:
        def __init__(self):
            self._calls = 0

        def is_set(self):
            return self._calls > 0

        def wait(self, _timeout=None):
            self._calls += 1
            return True

    monkeypatch.setattr(web_ui, "_account_mgr", Manager())
    monkeypatch.setattr(web_ui, "_stop_event", StopAfterFirstWait())
    monkeypatch.setattr(web_ui, "_emit_log", lambda level, msg, tag="": logs.append((level, msg)))
    monkeypatch.setattr(web_ui, "_update_state", lambda **kw: states.append(kw))
    monkeypatch.setattr(web_ui, "_scheduler_window_is_open", lambda now=None: True)

    web_ui._scheduler_cycle()

    warn = [msg for level, msg in logs if level == "warn"]
    assert warn, "无可调度账号必须留下告警日志"
    assert "非 active 1 个" in warn[0]
    assert "Errored(error)" in warn[0]


def test_scheduler_loop_marks_state_dead_on_crash(monkeypatch):
    logs = []
    states = []

    def boom():
        raise RuntimeError("scheduler exploded")

    monkeypatch.setattr(web_ui, "_scheduler_cycle", boom)
    monkeypatch.setattr(web_ui, "_emit_log", lambda level, msg, tag="": logs.append((level, msg)))
    monkeypatch.setattr(web_ui, "_update_state", lambda **kw: states.append(kw))

    try:
        web_ui._scheduler_loop()
    except RuntimeError:
        pass
    else:
        raise AssertionError("异常必须继续抛出以便线程栈可见")

    assert any(level == "error" for level, _ in logs)
    assert states and states[-1]["running"] is False
    assert "异常退出" in states[-1]["round_status"]


def test_state_endpoint_corrects_stale_running_flag(monkeypatch):
    class DeadThread:
        def is_alive(self):
            return False

    class Manager:
        def get_summary(self):
            return {"account_count": 1, "active_accounts": 1, "error_accounts": 0, "total_aliases": 3, "total_active_aliases": 3}

    monkeypatch.setattr(web_ui, "_account_mgr", Manager())
    monkeypatch.setattr(web_ui, "_scheduler_thread", DeadThread())
    web_ui._global_state["running"] = True
    client = web_ui.app.test_client()

    payload = client.get("/api/state").get_json()

    assert payload["running"] is False
    assert payload["scheduler_alive"] is False
    assert web_ui._global_state["running"] is False


# ---- 问题 2：批量创建报假成功 ----


def _sse_events(response):
    events = []
    for block in response.get_data(as_text=True).split("\n\n"):
        line = next((row for row in block.split("\n") if row.startswith("data:")), None)
        if line:
            events.append(json.loads(line[5:].strip()))
    return events

def test_batch_form_selects_active_accounts_and_submits():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
let submitHandler = null;
let runCalls = 0;
const form = {addEventListener(type, handler) { if (type === 'submit') submitHandler = handler; }};
const S = {
  accounts: [
    {id:'active-1', name:'Active', status:'active'},
    {id:'error-1', name:'Error', status:'error'},
  ],
  E(id) { return id === 'batchForm' ? form : null; },
  esc(value) { return String(value == null ? '' : value); },
  empty(value) { return value; },
  setTitle() {},
  view(html) { rendered = html; },
};
const context = {window:{HME:S}, document:{querySelectorAll(){ return []; }}, fetch, TextDecoder, console};
vm.runInNewContext(fs.readFileSync('static/js/05-inbox-docs.js', 'utf8'), context);
S.runBatch = async () => { runCalls += 1; };
S.renderBatch();
if (!rendered.includes('value="active-1" checked')) throw new Error('active account not selected by default');
if (!rendered.includes('value="error-1" disabled')) throw new Error('error account should stay disabled');
if (!submitHandler) throw new Error('batch form submit handler missing');
let prevented = false;
submitHandler({preventDefault(){ prevented = true; }});
if (!prevented || runCalls !== 1) throw new Error('batch submit did not execute exactly once');
''';
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_batch_create_reports_failure_not_success(monkeypatch):
    logs = []

    class FailingManager:
        accounts = {"a1": {"id": "a1", "status": "active"}}

        def create_aliases_batch(self, account_ids, count, interval, label):
            return {"a1": [{"email": None, "ok": False, "error": "reached the limit"}]}

    monkeypatch.setattr(web_ui, "_account_mgr", FailingManager())
    monkeypatch.setattr(web_ui, "_emit_log", lambda level, msg, tag="": logs.append((level, msg)))
    client = web_ui.app.test_client()

    payload = client.post("/api/create-batch", json={"account_ids": ["a1"], "count_per_account": 1}).get_json()

    assert payload["ok"] is False
    assert payload["total_created"] == 0
    assert "reached the limit" in payload["error"]
    assert not any(level == "success" for level, _ in logs), "0 成功不得记为 success"


def test_batch_stream_emits_per_item_events(monkeypatch):
    monkeypatch.setattr(web_ui, "_emit_log", lambda level, msg, tag="": {"level": level, "msg": msg})
    monkeypatch.setattr(web_ui, "_update_state", lambda **kw: None)
    monkeypatch.setattr(web_ui, "_increment_state", lambda **kw: None)

    class Manager:
        def get_account(self, acc_id):
            return {"id": acc_id, "name": "Main", "status": "active"}

    created = iter([(True, "one@icloud.com"), (True, "two@icloud.com")])
    monkeypatch.setattr(web_ui, "_account_mgr", Manager())
    monkeypatch.setattr(web_ui, "_create_scheduled_alias", lambda acc_id, name: next(created))
    client = web_ui.app.test_client()

    response = client.post("/api/create-batch-stream", json={"account_ids": ["a1"], "count_per_account": 2, "interval": 0})
    events = _sse_events(response)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    items = [e for e in events if e["type"] == "item"]
    assert [i["email"] for i in items] == ["one@icloud.com", "two@icloud.com"]
    assert events[-1] == {"type": "done", "total_created": 2, "total_errors": 0, "ok": True}


def test_batch_stream_stops_account_on_limit_error(monkeypatch):
    monkeypatch.setattr(web_ui, "_emit_log", lambda level, msg, tag="": {"level": level, "msg": msg})
    monkeypatch.setattr(web_ui, "_update_state", lambda **kw: None)
    monkeypatch.setattr(web_ui, "_increment_state", lambda **kw: None)

    class Manager:
        def get_account(self, acc_id):
            return {"id": acc_id, "name": "Main", "status": "active"}

    calls = []

    def create(acc_id, name):
        calls.append(acc_id)
        return False, "You have reached the limit of addresses you can create right now."

    monkeypatch.setattr(web_ui, "_account_mgr", Manager())
    monkeypatch.setattr(web_ui, "_create_scheduled_alias", create)
    client = web_ui.app.test_client()

    response = client.post("/api/create-batch-stream", json={"account_ids": ["a1"], "count_per_account": 5, "interval": 0})
    events = _sse_events(response)

    assert len(calls) == 1, "触达限制后必须停止该账号，避免继续撞墙"
    assert any(e["type"] == "account-stop" for e in events)
    assert events[-1]["ok"] is False


def test_batch_stream_rejects_inactive_account(monkeypatch):
    monkeypatch.setattr(web_ui, "_emit_log", lambda level, msg, tag="": {"level": level, "msg": msg})
    monkeypatch.setattr(web_ui, "_update_state", lambda **kw: None)

    class Manager:
        def get_account(self, acc_id):
            return {"id": acc_id, "name": "Broken", "status": "error"}

    monkeypatch.setattr(web_ui, "_account_mgr", Manager())
    monkeypatch.setattr(web_ui, "_create_scheduled_alias", lambda *a: (_ for _ in ()).throw(AssertionError("不应创建")))
    client = web_ui.app.test_client()

    response = client.post("/api/create-batch-stream", json={"account_ids": ["a1"], "count_per_account": 2})
    events = _sse_events(response)

    failed = [e for e in events if e["type"] == "item" and not e["ok"]]
    assert failed and "status=error" in failed[0]["error"]
    assert events[-1]["total_created"] == 0


def test_create_aliases_sleeps_between_attempts(monkeypatch, tmp_path):
    """连续无间隔创建必被限流，间隔不可为 0。"""
    sleeps = []
    monkeypatch.setattr(account_manager.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(account_manager, "CREATE_INTERVAL_SEC", 20.0)
    mgr = _isolated_manager(monkeypatch, tmp_path)
    account = mgr.add_account("main", "A=1", validate=False)

    class FakeHME:
        def __init__(self, *a, **kw):
            pass

        def create_alias(self, label=None, note=None, max_retries=3):
            return {"hme": "x@icloud.com"}

    monkeypatch.setitem(sys.modules, "icloud_hme", types.SimpleNamespace(ICloudHME=FakeHME))
    mgr.create_aliases_for_account(account["id"], count=3)

    assert sleeps == [20.0, 20.0], "3 次创建之间应有 2 段间隔"

def test_mail_probe_config_validates_and_persists(monkeypatch, tmp_path):
    config_file = tmp_path / "mail_probe_config.json"
    state_file = tmp_path / "mail_probe_state.json"
    monkeypatch.setattr(web_ui, "MAIL_PROBE_CONFIG_FILE", config_file)
    monkeypatch.setattr(web_ui, "MAIL_PROBE_STATE_FILE", state_file)
    started = []
    monkeypatch.setattr(web_ui, "_start_mail_probe_thread", lambda: started.append(True) or True)
    client = web_ui.app.test_client()

    saved = client.put(
        "/api/mail-probe/config",
        json={
            "enabled": True,
            "interval_minutes": 45,
            "start_time": "09:30",
            "end_time": "21:00",
            "limit_per": 3,
            "days": 7,
            "force": False,
            "account_ids": ["acc_missing"],
        },
    )
    assert saved.status_code == 200
    assert saved.json["config"] == {
        "enabled": True,
        "interval_minutes": 45,
        "start_time": "09:30",
        "end_time": "21:00",
        "limit_per": 3,
        "days": 7,
        "force": False,
        "account_ids": [],
    }
    assert started == [True]
    assert json.loads(config_file.read_text(encoding="utf-8"))["limit_per"] == 3

    for payload in (
        {"enabled": True, "start_time": "25:00"},
        {"enabled": True, "interval_minutes": 0},
        {"enabled": True, "limit_per": 99},
        {"enabled": True, "days": 0},
    ):
        assert client.put("/api/mail-probe/config", json=payload).status_code == 400

    # 未传的字段保留已存值，避免部分更新把间隔/时间窗重置成默认。
    partial = client.put("/api/mail-probe/config", json={"enabled": False})
    assert partial.status_code == 200
    assert partial.json["config"]["interval_minutes"] == 45
    assert partial.json["config"]["end_time"] == "21:00"


def test_mail_probe_toggle_keeps_watcher_thread_armed(monkeypatch, tmp_path):
    """停用后再启用不得让守护线程失活，否则自动探测永远不会触发。"""
    monkeypatch.setattr(web_ui, "MAIL_PROBE_CONFIG_FILE", tmp_path / "mail_probe_config.json")
    monkeypatch.setattr(web_ui, "MAIL_PROBE_STATE_FILE", tmp_path / "mail_probe_state.json")
    monkeypatch.setattr(web_ui, "_run_mail_probe_once", lambda config=None: True)
    client = web_ui.app.test_client()
    web_ui._stop_mail_probe_thread()
    web_ui._mail_probe_thread = None

    try:
        assert client.put("/api/mail-probe/config", json={"enabled": True}).json["state"]["running"] is True
        assert client.put("/api/mail-probe/config", json={"enabled": False}).json["state"]["thread_alive"] is True
        enabled = client.put("/api/mail-probe/config", json={"enabled": True})
        assert enabled.json["state"]["thread_alive"] is True
        assert enabled.json["state"]["running"] is True
    finally:
        web_ui._stop_mail_probe_thread()


def test_mail_probe_next_trigger_respects_window_and_interval():
    config = dict(web_ui.DEFAULT_MAIL_PROBE_CONFIG, start_time="08:00", end_time="20:00", interval_minutes=30)
    day = datetime(2026, 8, 26, tzinfo=web_ui.BEIJING_TZ)

    before = web_ui._mail_probe_next_trigger(day.replace(hour=6), config, None)
    inside = web_ui._mail_probe_next_trigger(day.replace(hour=9), config, day.replace(hour=8, minute=50).timestamp())
    after = web_ui._mail_probe_next_trigger(day.replace(hour=21), config, None)

    assert before == day.replace(hour=8)
    assert inside == day.replace(hour=9, minute=20)
    assert after == day.replace(hour=8) + timedelta(days=1)

def test_mail_probe_run_uses_mailbox_compatibility_path(monkeypatch):
    monkeypatch.setattr(web_ui, "_run_mail_probe_once", lambda: True)
    client = web_ui.app.test_client()

    response = client.post("/api/mailboxes/probe")

    assert response.status_code == 200
    assert response.json == {"ok": True, "queued": True}
