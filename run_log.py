#!/usr/bin/env python3
"""运行日志持久化 — 按天 JSONL 文件，超期自动清理。

每天一个 ``logs/app-YYYYMMDD.jsonl``，一行一条：
``{"seq":1,"date":"20260822","ts":"...","time":"19:00:00","level":"info","msg":"...","tag":""}``

保留天数由 ``HME_LOG_RETENTION_DAYS`` 控制（默认 7 天），进程启动时清理一次，
之后由调用方按天触发 :meth:`RunLogStore.cleanup`。
"""
import json, os, re, threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_RETENTION_DAYS = 7
FILE_PATTERN = re.compile(r"^app-(\d{8})\.jsonl$")


def resolve_retention_days(value: object = None) -> int:
    """保留天数：显式参数 > 环境变量 > 默认 7，最小 1 天。"""
    raw = value if value is not None else os.environ.get("HME_LOG_RETENTION_DAYS", "")
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    return max(1, days)


def date_key(moment: Optional[datetime] = None) -> str:
    return (moment or datetime.now()).strftime("%Y%m%d")


class RunLogStore:
    """线程安全的按天 JSONL 日志仓库。"""

    def __init__(self, log_dir, retention_days: object = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = resolve_retention_days(retention_days)
        self._lock = threading.Lock()

    # ---- 路径 ----

    def path_for(self, key: str) -> Path:
        return self.log_dir / f"app-{key}.jsonl"

    def available_dates(self) -> List[str]:
        keys = []
        for path in self.log_dir.glob("app-*.jsonl"):
            match = FILE_PATTERN.match(path.name)
            if match:
                keys.append(match.group(1))
        return sorted(keys, reverse=True)

    # ---- 写 ----

    def append(self, entry: Dict, moment: Optional[datetime] = None) -> None:
        key = str(entry.get("date") or "") or date_key(moment)
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self.path_for(key), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # ---- 读 ----

    def _iter_entries(self, key: str):
        path = self.path_for(key)
        if not path.exists():
            return
        with self._lock:
            raw = path.read_text(encoding="utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry

    def read(self, key: str = "", limit: int = 200, since: int = 0) -> List[Dict]:
        key = str(key or "") or date_key()
        limit = max(1, min(int(limit or 200), 2000))
        since = max(0, int(since or 0))
        entries = [e for e in self._iter_entries(key) if int(e.get("seq") or 0) > since]
        return entries[-limit:]

    def last_seq(self, key: str = "") -> int:
        """当天最大 seq，用于进程重启后续接序号。"""
        key = str(key or "") or date_key()
        return max((int(e.get("seq") or 0) for e in self._iter_entries(key)), default=0)

    # ---- 清理 ----

    def cleanup(self, moment: Optional[datetime] = None) -> List[str]:
        """删除超过保留天数的日志文件，返回被删掉的日期。"""
        cutoff = ((moment or datetime.now()) - timedelta(days=self.retention_days - 1)).strftime("%Y%m%d")
        removed = []
        for key in self.available_dates():
            if key >= cutoff:
                continue
            try:
                self.path_for(key).unlink()
                removed.append(key)
            except OSError:
                continue
        return removed
