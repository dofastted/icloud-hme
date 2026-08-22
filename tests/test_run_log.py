import queue
from datetime import datetime, timedelta

import web_ui
from run_log import RunLogStore, resolve_retention_days


def test_retention_days_env_and_floor(monkeypatch):
    monkeypatch.delenv("HME_LOG_RETENTION_DAYS", raising=False)
    assert resolve_retention_days() == 7
    monkeypatch.setenv("HME_LOG_RETENTION_DAYS", "3")
    assert resolve_retention_days() == 3
    assert resolve_retention_days(30) == 30
    assert resolve_retention_days(0) == 1
    assert resolve_retention_days("garbage") == 7


def test_store_appends_and_reads_by_date(tmp_path):
    store = RunLogStore(tmp_path, retention_days=7)
    store.append({"seq": 1, "date": "20260820", "level": "info", "msg": "old"})
    store.append({"seq": 1, "date": "20260821", "level": "warn", "msg": "new"})

    assert store.read("20260820")[0]["msg"] == "old"
    assert store.read("20260821")[0]["level"] == "warn"
    assert store.available_dates() == ["20260821", "20260820"]
    assert store.last_seq("20260821") == 1


def test_store_read_since_filters_earlier_entries(tmp_path):
    store = RunLogStore(tmp_path, retention_days=7)
    for seq in range(1, 6):
        store.append({"seq": seq, "date": "20260821", "level": "info", "msg": f"m{seq}"})

    entries = store.read("20260821", since=3)

    assert [e["seq"] for e in entries] == [4, 5]


def test_store_cleanup_removes_files_beyond_retention(tmp_path):
    store = RunLogStore(tmp_path, retention_days=3)
    now = datetime(2026, 8, 22)
    for offset in range(6):
        key = (now - timedelta(days=offset)).strftime("%Y%m%d")
        store.append({"seq": 1, "date": key, "level": "info", "msg": key})

    removed = store.cleanup(now)

    assert set(removed) == {"20260819", "20260818", "20260817"}
    assert store.available_dates() == ["20260822", "20260821", "20260820"]


def test_store_ignores_corrupt_lines(tmp_path):
    store = RunLogStore(tmp_path, retention_days=7)
    store.append({"seq": 1, "date": "20260821", "level": "info", "msg": "good"})
    with open(store.path_for("20260821"), "a", encoding="utf-8") as fh:
        fh.write("{not json\n\n")

    assert [e["msg"] for e in store.read("20260821")] == ["good"]


def _isolate_logs(monkeypatch, tmp_path, retention_days=7):
    store = RunLogStore(tmp_path, retention_days=retention_days)
    monkeypatch.setattr(web_ui, "_log_store", store)
    monkeypatch.setattr(web_ui, "_log_queue", queue.Queue())
    monkeypatch.setattr(web_ui, "_log_date", "20260822")
    monkeypatch.setattr(web_ui, "_log_seq", 0)
    web_ui._log_buffer.clear()
    return store


def test_emit_log_persists_entry_to_disk(monkeypatch, tmp_path):
    store = _isolate_logs(monkeypatch, tmp_path)
    monkeypatch.setattr(web_ui, "_now", lambda: datetime(2026, 8, 22, 10, 0, 0))

    web_ui._emit_log("info", "persisted line", tag="batch")

    written = store.read("20260822")
    assert len(written) == 1
    assert written[0]["msg"] == "persisted line"
    assert written[0]["tag"] == "batch"
    assert written[0]["date"] == "20260822"


def test_emit_log_rotates_and_cleans_on_new_day(monkeypatch, tmp_path):
    store = _isolate_logs(monkeypatch, tmp_path, retention_days=1)
    monkeypatch.setattr(web_ui, "_now", lambda: datetime(2026, 8, 22, 23, 59, 0))
    web_ui._emit_log("info", "day one")

    monkeypatch.setattr(web_ui, "_now", lambda: datetime(2026, 8, 23, 0, 0, 30))
    web_ui._emit_log("info", "day two")

    assert store.read("20260823")[0]["msg"] == "day two"
    assert store.read("20260823")[0]["seq"] == 1
    # retention_days=1 → 前一天的文件在跨天时被清掉
    assert store.available_dates() == ["20260823"]


def test_logs_endpoint_falls_back_to_disk_after_restart(monkeypatch, tmp_path):
    store = _isolate_logs(monkeypatch, tmp_path)
    store.append({"seq": 1, "date": "20260822", "time": "09:00:00", "level": "info", "msg": "before restart"})
    web_ui._log_buffer.clear()
    client = web_ui.app.test_client()

    payload = client.get("/api/logs").get_json()

    assert payload["ok"] is True
    assert payload["logs"][0]["msg"] == "before restart"
    assert payload["retention_days"] == 7


def test_logs_endpoint_reads_requested_past_date(monkeypatch, tmp_path):
    store = _isolate_logs(monkeypatch, tmp_path)
    store.append({"seq": 1, "date": "20260820", "time": "08:00:00", "level": "info", "msg": "two days ago"})
    monkeypatch.setattr(web_ui, "_now", lambda: datetime(2026, 8, 22, 10, 0, 0))
    web_ui._emit_log("info", "today line")
    client = web_ui.app.test_client()

    payload = client.get("/api/logs?date=20260820").get_json()

    assert [e["msg"] for e in payload["logs"]] == ["two days ago"]
    assert payload["date"] == "20260820"
    assert "20260820" in payload["dates"]
