#!/usr/bin/env python3
"""iCloud HME Web UI — 多账号聚合管理平台 — Flask single-page app."""
import sys, os, json, time, queue, secrets, threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import wraps
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0, str(HERE))

from flask import Flask, Response, request, jsonify, render_template, g
from icloud_hme import ICloudHME, extract_chrome_cookies
from account_manager import AccountManager, SCHEDULER_ALIAS_LIMIT, DEFAULT_GROUP_ID, UNAVAILABLE_GROUP_ID, DEPRECATED_GROUP_ID, account_alias_total, account_reached_scheduler_limit, scheduler_eligible_accounts, scheduler_count_needs_refresh, account_has_mail_config, account_mail_email, account_mail_host, infer_mail_host
from api_keys import APIKeyStore, extract_api_key
from mailbox_service import IMAPNotConfigured, IMAPUnavailable, MailboxNotFound, MailboxService
from shared_mailboxes import SharedMailboxStore

# ---- config ----
RESULTS_DIR = HERE / "results"
LOGS_DIR = HERE / "logs"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
SCHEDULER_WINDOW_START_HOUR = 7
SCHEDULER_WINDOW_END_HOUR = 20
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


API_AVAILABLE_EXCLUDED_GROUP_IDS = {UNAVAILABLE_GROUP_ID, DEPRECATED_GROUP_ID}
app = Flask(__name__)
_log_queue = queue.Queue()
_log_buffer = deque(maxlen=500)
_log_lock = threading.Lock()
_log_seq = 0
_today_key = datetime.now().strftime("%Y%m%d")
_global_state = {"running":False,"creating":False,"round_status":"","total_created":0,"today_created":0,"current_round_created":0,"next_trigger":None,"last_error":None,"cookies_ok":False,"alias_count":0,"alias_active":0}
_lock = threading.Lock()
_validation_queue = queue.Queue()
_validation_thread = None
_validation_lock = threading.Lock()
_validation_jobs = {}
_scheduler_thread = None
_stop_event = threading.Event()
_account_mgr = AccountManager()
_api_keys = APIKeyStore()
_shared_store = SharedMailboxStore()
_mailbox_service = MailboxService(_account_mgr, _shared_store)

@app.errorhandler(KeyError)
def _handle_key_error(err):
    return jsonify({"ok":False,"error":str(err).strip("'")}), 404

@app.errorhandler(ValueError)
def _handle_value_error(err):
    return jsonify({"ok":False,"error":str(err)}), 400

@app.errorhandler(RuntimeError)
def _handle_runtime_error(err):
    return jsonify({"ok":False,"error":str(err)}), 502

@app.errorhandler(MailboxNotFound)
def _handle_mailbox_not_found(err):
    return jsonify({"ok":False,"error":str(err).strip("'") or "not found"}), 404

@app.errorhandler(IMAPNotConfigured)
def _handle_imap_not_configured(err):
    return jsonify({"ok":False,"error":str(err)}), 400

@app.errorhandler(IMAPUnavailable)
def _handle_imap_unavailable(err):
    return jsonify({"ok":False,"error":str(err)}), 502

_RATE_LIMIT_KW = ["limit","exceeded","maximum","quota","429","too many","try again","unavailable","上限","超过","过多","频繁","rate limit","throttle","blocked"]

def _is_limit_error(err: str) -> bool: return any(kw in err.lower() for kw in _RATE_LIMIT_KW)

def _require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = extract_api_key(request.headers)
        record = _api_keys.verify(key)
        if not record:
            return jsonify({"ok":False,"error":"invalid API key"}), 401
        g.api_key = record
        return fn(*args, **kwargs)
    return wrapper

def _safe_account(account):
    safe = {k:v for k,v in account.items() if k not in ("cookies","app_password","icloud_email","mail_password","group_id","group_name","group_color","session_fingerprint")}
    get_settings = getattr(_account_mgr, "get_account_mail_settings", None)
    if callable(get_settings):
        settings = get_settings(account)
        safe.update(settings)
    else:
        safe["has_mail_config"] = account_has_mail_config(account)
        safe["mail_email"] = account_mail_email(account)
        safe["mail_host"] = account_mail_host(account)
        safe["mail_port"] = int(account.get("mail_port") or 993)
        safe["imap_config_id"] = ""
        safe["imap_config_name"] = ""
    return safe

def _list_imap_configs_safe():
    list_configs = getattr(_account_mgr, "list_imap_configs", None)
    if not callable(list_configs):
        return []
    return list_configs()

def _list_groups_safe():
    list_groups = getattr(_account_mgr, "list_groups", None)
    if not callable(list_groups):
        return []
    return list_groups()

def _groups_with_mailbox_counts():
    groups = [dict(group) for group in _list_groups_safe()]
    if not groups:
        return []
    default_id = next((group.get("id") for group in groups if group.get("is_default")), DEFAULT_GROUP_ID)
    counts = {group.get("id"): 0 for group in groups if group.get("id")}
    try:
        for mailbox in _mailbox_service.list_mailboxes():
            gid = mailbox.get("group_id") or default_id
            if gid not in counts:
                gid = default_id
            counts[gid] = counts.get(gid, 0) + 1
    except Exception:
        pass
    for group in groups:
        group["mailbox_count"] = counts.get(group.get("id"), group.get("mailbox_count", 0))
    return groups

class _RateLimiter:
    def __init__(self):
        self._hits = {}
        self._lock = threading.RLock()

    def allow(self, bucket: str, limit: int, window_sec: int = 60) -> bool:
        now = time.time()
        cutoff = now - window_sec
        with self._lock:
            hits = [ts for ts in self._hits.get(bucket, []) if ts >= cutoff]
            if len(hits) >= limit:
                self._hits[bucket] = hits
                return False
            hits.append(now)
            self._hits[bucket] = hits
            return True

_shared_rate_limiter = _RateLimiter()

def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"

def _paginate(items, default_limit=50, max_limit=100):
    limit = min(max(request.args.get("limit", default_limit, type=int), 1), max_limit)
    offset = max(request.args.get("offset", 0, type=int), 0)
    return items[offset:offset + limit], limit, offset

def _optional_int(value):
    if value in (None, ""):
        return None
    return int(value)

def _set_mail_settings_for_account(acc_id, data):
    imap_config_id = data.get("imap_config_id") or ""
    if imap_config_id:
        return _account_mgr.set_mail_settings(
            acc_id,
            "",
            "",
            "",
            993,
            imap_config_id=imap_config_id,
        )
    return _account_mgr.set_mail_settings(
        acc_id,
        data.get("email") or data.get("mail_email") or "",
        data.get("password") or data.get("mail_password") or "",
        data.get("host") or data.get("mail_host") or "",
        data.get("port") or data.get("mail_port") or 993,
    )

def _mail_settings_response(account, result):
    ok = bool(result.get("ok"))
    payload = {"ok":ok,"account":_safe_account(account),"mail":result}
    if not ok:
        payload["error"] = result.get("error") or "邮件读取暂不可用"
    return jsonify(payload)

def _mail_test_request():
    data = request.get_json(silent=True) or {}
    alias = str(data.get("alias") or request.args.get("alias", "") or "").strip()
    limit = min(max(int(data.get("limit") or request.args.get("limit", 5, type=int) or 5), 1), 20)
    days = min(max(int(data.get("days") or request.args.get("days", 7, type=int) or 7), 1), 90)
    return alias, limit, days

def _mail_test_response(acc_id):
    alias, limit, days = _mail_test_request()
    result = _account_mgr.test_mail_read(acc_id, alias_email=alias, limit=limit, days=days)
    payload = {"ok":bool(result.get("ok")),"mail":result}
    if not payload["ok"]:
        payload["error"] = result.get("error") or "邮件读取暂不可用"
    return jsonify(payload)

def _shared_entry_url() -> str:
    exact = os.environ.get("SHARED_PUBLIC_URL", "").strip()
    if exact:
        return exact.rstrip("/")
    base = (
        os.environ.get("SHARED_PUBLIC_BASE_URL", "").strip()
        or os.environ.get("PUBLIC_SHARED_BASE_URL", "").strip()
    )
    if base:
        return base.rstrip("/") + "/shared"
    return request.url_root.rstrip("/") + "/shared"

def _legacy_share_url(raw_key: str) -> str:
    return request.url_root.rstrip("/") + "/shared/" + raw_key

def _share_create_payload(created: dict, raw_key: str) -> dict:
    return {
        "ok": True,
        "shared": created,
        "redemption_code": raw_key,
        "share_key": raw_key,
        "share_url": _shared_entry_url(),
        "legacy_share_url": _legacy_share_url(raw_key),
    }

def _api_base_url() -> str:
    return request.url_root.rstrip("/") + "/api/v1"

def _api_config_payload() -> dict:
    summary = _account_mgr.get_summary()
    available_count = len(_available_hme_items(refresh=False))
    return {
        "ok": True,
        "api": {
            "version": "v1",
            "base_url": _api_base_url(),
            "auth": {
                "type": "api_key",
                "headers": ["Authorization: Bearer <api_key>", "X-API-Key: <api_key>"],
                "key_prefix": g.api_key.get("prefix", "") if getattr(g, "api_key", None) else "",
            },
            "entrypoints": {
                "config": "/api/v1/config",
                "available_hme": "/api/v1/hme/available",
                "next_hme": "/api/v1/hme/available/next",
                "hme_latest": "/api/v1/hme/{alias_email}/latest?force=0",
                "mailbox_messages": "/api/v1/mailboxes/{alias_email}/messages?limit=1&force=0",
                "shared_redemption_create": "/api/v1/shared-mailboxes",
            },
        },
        "shared": {"entry_url": _shared_entry_url()},
        "capabilities": {
            "global_hme_lookup": True,
            "latest_mail": True,
            "refresh_mail": True,
            "redemption_codes": True,
        },
        "counts": {
            "accounts": summary.get("account_count", 0),
            "active_accounts": summary.get("active_accounts", 0),
            "available_hme": available_count,
        },
    }

def _shared_not_found_response():
    return jsonify({"ok":False,"error":"not found"}), 404

def _shared_record_for_key(raw_key: str):
    if not _shared_rate_limiter.allow("ip:" + _client_ip(), 30):
        return None, (jsonify({"ok":False,"error":"rate limited"}), 429)
    if not _shared_rate_limiter.allow("key:" + raw_key, 10):
        return None, (jsonify({"ok":False,"error":"rate limited"}), 429)
    share = _shared_store.verify(raw_key)
    if not share:
        return None, _shared_not_found_response()
    return share, None

def _alias_contract(alias):
    return {
        "hme": alias.get("hme") or alias.get("email") or "",
        "label": alias.get("label", ""),
        "note": alias.get("note", ""),
        "isActive": bool(alias.get("isActive", alias.get("active", True))),
        "createTimestamp": alias.get("createTimestamp") or alias.get("createdAt"),
        "anonymousId": alias.get("anonymousId", ""),
        "forwardToEmail": alias.get("forwardToEmail", ""),
        "origin": alias.get("origin", ""),
    }

_time_offset = 0.0
def _sync_time():
    global _time_offset
    for url in ["https://www.baidu.com","https://www.cloudflare.com","https://www.microsoft.com"]:
        try:
            import requests as _r
            resp = _r.head(url, timeout=5)
            date_str = resp.headers.get("Date","")
            if date_str:
                from email.utils import parsedate_to_datetime
                net_time = parsedate_to_datetime(date_str)
                current = datetime.now(net_time.tzinfo) if net_time.tzinfo else datetime.now()
                _time_offset = (net_time - current).total_seconds()
                return _time_offset
        except Exception:
            continue
    return 0.0

def _now() -> datetime: return datetime.now() + timedelta(seconds=_time_offset)

def _beijing_now() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(seconds=_time_offset)).astimezone(BEIJING_TZ)

def _scheduler_window_is_open(now: datetime | None = None) -> bool:
    current = now or _beijing_now()
    return SCHEDULER_WINDOW_START_HOUR <= current.hour < SCHEDULER_WINDOW_END_HOUR

def _next_scheduler_window_start(now: datetime | None = None) -> datetime:
    current = now or _beijing_now()
    if current.hour < SCHEDULER_WINDOW_START_HOUR:
        return current.replace(hour=SCHEDULER_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    return (current + timedelta(days=1)).replace(hour=SCHEDULER_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)

def _emit_log(level, msg):
    global _log_seq
    entry = {"time":_now().strftime("%H:%M:%S"),"level":level,"msg":msg}
    with _log_lock:
        _log_seq += 1
        entry["seq"] = _log_seq
        _log_buffer.append(dict(entry))
    _log_queue.put(entry)


def _log_entries(limit: int = 200, since: int = 0):
    limit = max(1, min(int(limit or 200), 500))
    since = max(0, int(since or 0))
    with _log_lock:
        entries = [dict(item) for item in _log_buffer if int(item.get("seq") or 0) > since]
    return entries[-limit:]


def _start_validation_worker():
    global _validation_thread
    with _validation_lock:
        if _validation_thread and _validation_thread.is_alive():
            return
        _validation_thread = threading.Thread(target=_validation_loop, daemon=True)
        _validation_thread.start()


def _queue_account_validation(acc_id: str, reason: str = "manual") -> bool:
    account = _account_mgr.get_account(acc_id)
    if not account:
        raise KeyError(f"账号不存在: {acc_id}")
    version = int(account.get("session_version") or 0)
    with _validation_lock:
        if _validation_jobs.get(acc_id) == version:
            return False
        _validation_jobs[acc_id] = version
    _account_mgr.update_account(
        acc_id,
        validation_status="queued",
        validation_queued_at=_now().isoformat(),
    )
    _start_validation_worker()
    _validation_queue.put({"account_id":acc_id,"version":version,"reason":reason})
    return True


def _validation_loop():
    while True:
        job = _validation_queue.get()
        acc_id = job.get("account_id", "")
        version = int(job.get("version") or 0)
        reason = str(job.get("reason") or "manual")
        try:
            account = _account_mgr.get_account(acc_id)
            if not account or int(account.get("session_version") or 0) != version:
                continue
            _account_mgr.update_account(
                acc_id,
                validation_status="running",
                validation_started_at=_now().isoformat(),
            )
            _emit_log("info", f"后台校验开始 [{account.get('name', acc_id)}] ({reason})")
            updated = _account_mgr.validate_account(
                acc_id,
                expected_session_version=version,
                reason=reason,
            )
            label = updated.get("name") or acc_id
            status = updated.get("status")
            validation_status = updated.get("validation_status")
            if status == "active" and validation_status == "ok":
                _emit_log("success", f"后台校验通过 [{label}]")
            elif status == "active" and validation_status == "degraded":
                _emit_log(
                    "warn",
                    f"后台校验暂时失败，保持可用 [{label}]: "
                    f"{str(updated.get('last_error') or '')[:100]}",
                )
            else:
                _emit_log(
                    "warn",
                    f"后台校验失败 [{label}]: "
                    f"{str(updated.get('last_error') or '')[:100]}",
                )
        except Exception as exc:
            _emit_log("warn", f"后台校验异常 [{acc_id}]: {str(exc)[:100]}")
        finally:
            with _validation_lock:
                if _validation_jobs.get(acc_id) == version:
                    del _validation_jobs[acc_id]
            _validation_queue.task_done()

def _update_state(**kw):
    global _today_key
    with _lock:
        today = _now().strftime("%Y%m%d")
        if today != _today_key: _global_state["today_created"] = 0; _today_key = today
        _global_state.update(kw)

def _increment_state(**kw):
    global _today_key
    with _lock:
        today = _now().strftime("%Y%m%d")
        if today != _today_key: _global_state["today_created"] = 0; _today_key = today
        for k, delta in kw.items(): _global_state[k] = _global_state.get(k,0) + delta

def _refresh_scheduler_account_count(account: dict) -> dict:
    if not scheduler_count_needs_refresh(account):
        return account
    acc_id = account["id"]
    try:
        aliases = _account_mgr.get_aliases_for_account(acc_id)
    except Exception as exc:
        _emit_log("warn", f"[{account.get('name', acc_id)}] 邮箱数量刷新失败，使用本地数量: {str(exc)[:80]}")
        return account
    alias_total = len(aliases)
    alias_active = sum(1 for alias in aliases if alias.get("active") or alias.get("isActive"))
    updated = _account_mgr.update_account(acc_id, alias_total=alias_total, alias_active=alias_active, last_error=None)
    if updated:
        account.update(updated)
    return account


def _create_scheduled_alias(acc_id: str, acc_name: str) -> tuple[bool, str]:
    label = f"{acc_name} {_beijing_now().strftime('%m%d%H%M')}"
    results = _account_mgr.create_aliases_for_account(acc_id, count=1, label=label)
    if not results:
        return False, "create_aliases_for_account 返回空结果"
    result = results[0]
    email = result.get("email") or result.get("hme") or ""
    if result.get("ok") and email:
        return True, email
    return False, str(result.get("error") or "create_alias 返回空邮箱")



def _scheduler_loop():
    """后台调度器：北京时间 7:00-20:00，随机间隔 60-90min，每账号随机 3-5 个。"""
    import random as _random
    _update_state(running=True, round_status="等待触发窗口")
    _emit_log("info", f"调度器已启动 (BJ 7-20h, 间隔 60-90min, 每轮 3-5 个，单账号达到 {SCHEDULER_ALIAS_LIMIT} 跳过)")
    while not _stop_event.is_set():
        bj_now = _beijing_now()
        if not _scheduler_window_is_open(bj_now):
            next_start = _next_scheduler_window_start(bj_now)
            wait_sec = min(1800, max(1, int((next_start - bj_now).total_seconds())))
            _update_state(
                round_status=f"非窗口时段 (BJ {bj_now.hour}:00)，等待 {next_start.strftime('%m-%d %H:%M')}...",
                next_trigger=next_start.timestamp(),
            )
            _stop_event.wait(wait_sec)
            continue
        accounts = _account_mgr.list_accounts()
        active_accounts = scheduler_eligible_accounts(accounts)
        skipped = len([a for a in accounts if a.get("status") == "active" and account_reached_scheduler_limit(a)])
        if not active_accounts:
            _update_state(creating=False, round_status=f"无可调度账号，已达到上限 {skipped} 个")
            _stop_event.wait(1800)
            continue
        round_total = 0
        round_note = ""
        for i, account in enumerate(active_accounts):
            if _stop_event.is_set(): break
            acc_id = account["id"]; acc_name = account.get("name", acc_id)
            account = _refresh_scheduler_account_count(account)
            if account_reached_scheduler_limit(account):
                _emit_log("info", f"[{acc_name}] 已有 {account_alias_total(account)}/{SCHEDULER_ALIAS_LIMIT} 个邮箱，跳过调度")
                continue
            remaining = SCHEDULER_ALIAS_LIMIT - account_alias_total(account)
            target_count = min(_random.randint(3, 5), remaining)
            _emit_log("info", f"[{acc_name}] 本轮目标 {target_count} 个，当前 {account_alias_total(account)}/{SCHEDULER_ALIAS_LIMIT}")
            created = 0; errors = 0
            while created < target_count and errors < 3 and not _stop_event.is_set():
                ok, message = _create_scheduled_alias(acc_id, acc_name)
                if ok:
                    created += 1; round_total += 1
                    _emit_log("success", f"[{acc_name}] ({created}/{target_count}) {message}")
                    _increment_state(today_created=1, total_created=1)
                    account["alias_total"] = account.get("alias_total",0)+1
                    account["alias_active"] = account.get("alias_active",0)+1
                    errors = 0
                    if _stop_event.wait(_random.uniform(15,45)):
                        break
                    continue
                errors += 1
                if _is_limit_error(message):
                    round_note = f"[{acc_name}] 触达创建限制: {message[:120]}"
                    _update_state(last_error=round_note)
                    _emit_log("info",f"[{acc_name}] 触达上限: {message[:60]}")
                    break
                _update_state(last_error=message[:300])
                _emit_log("warn",f"[{acc_name}] 失败: {message[:80]}")
            if i < len(active_accounts)-1 and _stop_event.wait(_random.uniform(120,300)): break
        status = f"本轮创建 {round_total} 个" + (f"；{round_note}" if round_note else "")
        _update_state(creating=False, current_round_created=round_total, round_status=status)
        interval_sec = _random.randint(3600,5400)
        target = _now() + timedelta(seconds=interval_sec)
        _update_state(next_trigger=target.timestamp())
        _emit_log("info", f"下轮 {target.strftime('%H:%M')} (间隔 {interval_sec//60}min)")
        _stop_event.wait(interval_sec)
    _update_state(running=False, next_trigger=None, round_status="已停止")
    _emit_log("info", "调度器已停止")

def _health_loop():
    _error_reported = set()
    # After reboot, proxy/network may not be ready; avoid immediate demotion storms.
    first_delay_sec = int(os.environ.get("HME_HEALTH_FIRST_DELAY_SEC", "600"))
    interval_sec = int(os.environ.get("HME_HEALTH_INTERVAL_SEC", "1800"))
    if _stop_event.wait(max(60, first_delay_sec)):
        return
    while not _stop_event.is_set():
        for account in _account_mgr.list_accounts():
            if account.get("status") != "active":
                continue
            try:
                queued = _queue_account_validation(account["id"], "health")
                if queued:
                    _error_reported.discard(account["id"])
            except Exception as e:
                if account["id"] not in _error_reported:
                    _emit_log(
                        "warn",
                        f"健康检查排队失败 [{account.get('name', '?')}]: {str(e)[:100]}",
                    )
                    _error_reported.add(account["id"])
        if _stop_event.wait(max(300, interval_sec)):
            break

# ----- Flask Routes -----

@app.route("/")
@app.route("/index.html")
def index(): return render_template("index.html")

@app.route("/api/keys", methods=["POST"])
def api_keys_create():
    if _api_keys.has_keys() and not _api_keys.verify(extract_api_key(request.headers)):
        return jsonify({"ok":False,"error":"invalid API key"}), 401
    data = request.get_json() or {}
    key = _api_keys.create(data.get("name", "default"))
    return jsonify({"ok":True,"key":key})

@app.route("/api/keys")
@_require_api_key
def api_keys_list():
    return jsonify({"ok":True,"keys":_api_keys.list()})

@app.route("/api/keys/<key_id>/revoke", methods=["POST"])
@_require_api_key
def api_keys_revoke(key_id):
    return jsonify({"ok":_api_keys.deactivate(key_id)})

@app.route("/api/v1/accounts", methods=["GET"])
@_require_api_key
def api_v1_accounts():
    accounts = []
    for account in _account_mgr.list_accounts():
        item = _safe_account(account)
        item["has_cookies"] = bool(account.get("cookies"))
        accounts.append(item)
    groups = _groups_with_mailbox_counts()
    imap_configs = _list_imap_configs_safe()
    return jsonify({"ok":True,"accounts":accounts,"count":len(accounts),"groups":groups,"imap_configs":imap_configs})

@app.route("/api/v1/accounts", methods=["POST"])
@_require_api_key
def api_v1_add_account():
    data = request.get_json() or {}
    cookie_input = data.get("cookie_input", "")
    if not cookie_input:
        return jsonify({"ok":False,"error":"cookie_input is required"}), 400
    account = _account_mgr.add_account(
        data.get("name", "未命名账号"),
        cookie_input,
        data.get("host", "icloud.com"),
        validate=False,
    )
    queued = _queue_account_validation(account["id"], "api-add")
    return jsonify({"ok":True,"queued":queued,"account":_safe_account(_account_mgr.get_account(account["id"]) or account)})

@app.route("/api/v1/accounts/<acc_id>/session/validate", methods=["POST"])
@_require_api_key
def api_v1_validate_session(acc_id):
    queued = _queue_account_validation(acc_id, "api-validate")
    account = _account_mgr.get_account(acc_id) or {}
    return jsonify({"ok":True,"queued":queued,"account":_safe_account(account)})

@app.route("/api/v1/accounts/<acc_id>/mail-settings", methods=["POST"])
@_require_api_key
def api_v1_set_mail_settings(acc_id):
    data = request.get_json() or {}
    try:
        account = _set_mail_settings_for_account(acc_id, data)
        result = _account_mgr.test_mail_connection(acc_id)
        return _mail_settings_response(account, result)
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400

@app.route("/api/v1/accounts/<acc_id>/mail-settings/test", methods=["GET", "POST"])
@_require_api_key
def api_v1_test_mail_settings(acc_id):
    try:
        return _mail_test_response(acc_id)
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400

@app.route("/api/v1/accounts/<acc_id>/aliases", methods=["GET"])
@_require_api_key
def api_v1_aliases(acc_id):
    aliases = [_alias_contract(a) for a in _account_mgr.get_aliases_for_account(acc_id)]
    return jsonify({"ok":True,"aliases":aliases,"count":len(aliases)})

@app.route("/api/v1/accounts/<acc_id>/hme/generate", methods=["POST"])
@_require_api_key
def api_v1_generate_alias(acc_id):
    result = _account_mgr.generate_alias_candidate(acc_id)
    return jsonify({"ok":True,"result":result})

@app.route("/api/v1/accounts/<acc_id>/hme/reserve", methods=["POST"])
@_require_api_key
def api_v1_reserve_alias(acc_id):
    data = request.get_json() or {}
    hme = data.get("hme", "")
    if not hme:
        return jsonify({"ok":False,"error":"hme is required"}), 400
    alias = _account_mgr.reserve_alias_for_account(
        acc_id,
        hme,
        data.get("label", ""),
        data.get("note", ""),
    )
    return jsonify({"ok":True,"alias":_alias_contract(alias)})

@app.route("/api/v1/accounts/<acc_id>/aliases", methods=["POST"])
@_require_api_key
def api_v1_create_alias(acc_id):
    data = request.get_json() or {}
    results = _account_mgr.create_aliases_for_account(
        acc_id,
        min(int(data.get("count", 1)), 50),
        data.get("label", ""),
        data.get("note", ""),
    )
    aliases = [_alias_contract(r) for r in results if r.get("ok")]
    errors = [r.get("error") for r in results if not r.get("ok")]
    return jsonify({"ok":bool(aliases),"aliases":aliases,"count":len(aliases),"errors":errors})

@app.route("/api/v1/accounts/<acc_id>/aliases/<anonymous_id>/deactivate", methods=["POST"])
@_require_api_key
def api_v1_deactivate_alias(acc_id, anonymous_id):
    return jsonify({"ok":_account_mgr.deactivate_alias_for_account(acc_id, anonymous_id)})

def _available_hme_item(item: dict, accounts: dict) -> dict:
    account = accounts.get(item.get("account_id"), {})
    return {
        "hme": item.get("alias_email", ""),
        "alias_email": item.get("alias_email", ""),
        "account_id": item.get("account_id", ""),
        "account_name": item.get("account_name", ""),
        "label": item.get("label", ""),
        "group_id": item.get("group_id", DEFAULT_GROUP_ID),
        "group_name": item.get("group_name", ""),
        "group_color": item.get("group_color", ""),
        "created_at": item.get("created_at", ""),
        "is_active": bool(item.get("is_active")),
        "can_read_mail": account.get("status") == "active",
        "mail_status": "available" if account.get("status") == "active" else "unavailable",
        "shared": item.get("shared"),
    }


def _available_hme_items(refresh: bool = False) -> list:
    account_id = request.args.get("account_id", "") if request else ""
    q = request.args.get("q", "") if request else ""
    items = _mailbox_service.list_mailboxes(
        q=q,
        account_id=account_id,
        status="active",
        refresh=refresh,
    )
    accounts = {a.get("id"): a for a in _account_mgr.list_accounts()}
    result = []
    for item in items:
        account = accounts.get(item.get("account_id"), {})
        if account.get("status") != "active":
            continue
        if item.get("group_id") in API_AVAILABLE_EXCLUDED_GROUP_IDS:
            continue
        result.append(_available_hme_item(item, accounts))
    return result

def _available_hme_payload(single: bool = False) -> dict:
    refresh = request.args.get("refresh", "0") == "1"
    include_latest = request.args.get("include_latest", "0") == "1"
    force = request.args.get("force", "0") == "1"
    items = _available_hme_items(refresh=refresh)
    total = len(items)
    page, limit, offset = _paginate(items, default_limit=25, max_limit=100)
    if single:
        page = page[:1]
    if include_latest:
        for item in page:
            try:
                item["latest_message"] = _mailbox_service.get_latest_message(item["alias_email"], force=force)
            except (IMAPNotConfigured, IMAPUnavailable):
                item["latest_message"] = None
                item["mail_status"] = "not_ready"
            except MailboxNotFound:
                item["latest_message"] = None
                item["mail_error"] = "mailbox not found"
    payload = {"ok": True, "hme": page, "count": len(page), "total": total, "limit": limit, "offset": offset, "refreshed": refresh}
    if single:
        payload["item"] = page[0] if page else None
    return payload

@app.route("/api/v1/config")
@_require_api_key
def api_v1_config():
    return jsonify(_api_config_payload())

@app.route("/api/v1/client-config")
@_require_api_key
def api_v1_client_config():
    return jsonify(_api_config_payload())

@app.route("/api/v1/hme/available")
@_require_api_key
def api_v1_hme_available():
    return jsonify(_available_hme_payload())

@app.route("/api/v1/hme/available/next")
@_require_api_key
def api_v1_hme_available_next():
    return jsonify(_available_hme_payload(single=True))

@app.route("/api/v1/hme/<path:alias_email>/latest")
@_require_api_key
def api_v1_hme_latest(alias_email):
    force = request.args.get("force", "0") == "1"
    mailbox = _mailbox_service.get_mailbox(alias_email)
    try:
        message = _mailbox_service.get_latest_message(alias_email, force=force)
    except (IMAPNotConfigured, IMAPUnavailable):
        message = None
    return jsonify({"ok":True,"hme":mailbox["alias_email"],"mailbox":mailbox,"message":message})

@app.route("/api/v1/accounts/<acc_id>/aliases/<anonymous_id>", methods=["DELETE"])
@_require_api_key
def api_v1_delete_alias(acc_id, anonymous_id):
    return jsonify({"ok":_account_mgr.delete_alias_for_account(acc_id, anonymous_id)})


@app.route("/api/v1/accounts/<acc_id>/verification-codes")
@_require_api_key
def api_v1_verification_codes(acc_id):
    alias = request.args.get("alias", "").strip()
    limit = min(request.args.get("limit", 10, type=int), 50)
    days = min(request.args.get("days", 1, type=int), 30)
    codes = _account_mgr.get_verification_codes(acc_id, alias, limit=limit, days=days)
    return jsonify({"ok":True,"codes":codes,"count":len(codes),"alias":alias})

def _mailbox_list_payload():
    refresh = request.args.get("refresh", "0") == "1"
    items = _mailbox_service.list_mailboxes(
        q=request.args.get("q", ""),
        account_id=request.args.get("account_id", "") or request.args.get("account", ""),
        group_id=request.args.get("group_id", "") or request.args.get("group", ""),
        status=request.args.get("status", ""),
        refresh=refresh,
    )
    total = len(items)
    page, limit, offset = _paginate(items)
    meta = _mailbox_service.local_metadata()
    meta["refreshed"] = refresh
    return {"ok":True,"mailboxes":page,"count":len(page),"total":total,"limit":limit,"offset":offset,"refreshed":refresh,"source":meta["source"],"index_updated_at":meta.get("index_updated_at"),"local_alias_count":meta.get("local_alias_count",0),"index_alias_count":meta.get("index_alias_count",0)}

@app.route("/api/v1/mailboxes")
@_require_api_key
def api_v1_mailboxes():
    return jsonify(_mailbox_list_payload())

@app.route("/api/v1/mailboxes/search")
@_require_api_key
def api_v1_mailboxes_search():
    return jsonify(_mailbox_list_payload())

@app.route("/api/v1/mailboxes/<path:alias_email>")
@_require_api_key
def api_v1_mailbox_detail(alias_email):
    return jsonify({"ok":True,"mailbox":_mailbox_service.get_mailbox(alias_email)})

@app.route("/api/v1/mailboxes/<path:alias_email>/messages")
@_require_api_key
def api_v1_mailbox_messages(alias_email):
    limit = min(max(request.args.get("limit", 1, type=int), 1), 10)
    force = request.args.get("force", "0") == "1"
    messages = _mailbox_service.get_messages(alias_email, limit=limit, force=force)
    return jsonify({"ok":True,"messages":messages,"count":len(messages)})

@app.route("/api/v1/mailboxes/<path:alias_email>/messages/<path:message_id>")
@_require_api_key
def api_v1_mailbox_message_detail(alias_email, message_id):
    return jsonify({"ok":True,"message":_mailbox_service.get_message_detail(alias_email, message_id)})

@app.route("/api/v1/shared-mailboxes", methods=["POST"])
@_require_api_key
def api_v1_shared_mailboxes_create():
    data = request.get_json() or {}
    alias = data.get("alias_email", "")
    mailbox = _mailbox_service.get_mailbox(alias)
    created = _shared_store.create(mailbox["account_id"], mailbox["alias_email"])
    raw_key = created.pop("share_key")
    return jsonify(_share_create_payload(created, raw_key))

@app.route("/api/v1/shared-mailboxes")
@_require_api_key
def api_v1_shared_mailboxes():
    return jsonify({"ok":True,"shared":_shared_store.list(),"count":len(_shared_store.list())})

@app.route("/api/v1/shared-mailboxes/<share_id>/revoke", methods=["POST"])
@_require_api_key
def api_v1_shared_mailboxes_revoke(share_id):
    ok = _shared_store.revoke(share_id)
    return jsonify({"ok":ok})

@app.route("/api/mailboxes")
def api_mailboxes():
    return jsonify(_mailbox_list_payload())

@app.route("/api/mailboxes/<path:alias_email>")
def api_mailbox_detail(alias_email):
    return jsonify({"ok":True,"mailbox":_mailbox_service.get_mailbox(alias_email)})

@app.route("/api/mailboxes/<path:alias_email>/messages")
def api_mailbox_messages(alias_email):
    limit = min(max(request.args.get("limit", 1, type=int), 1), 10)
    force = request.args.get("force", "0") == "1"
    messages = _mailbox_service.get_messages(alias_email, limit=limit, force=force)
    return jsonify({"ok":True,"messages":messages,"count":len(messages)})

@app.route("/api/mailboxes/<path:alias_email>/messages/<path:message_id>")
def api_mailbox_message_detail(alias_email, message_id):
    return jsonify({"ok":True,"message":_mailbox_service.get_message_detail(alias_email, message_id)})

@app.route("/api/mailboxes/<path:alias_email>/share", methods=["POST"])
def api_mailbox_share(alias_email):
    mailbox = _mailbox_service.get_mailbox(alias_email)
    created = _shared_store.create(mailbox["account_id"], mailbox["alias_email"])
    raw_key = created.pop("share_key")
    return jsonify(_share_create_payload(created, raw_key))

@app.route("/api/shared")
def api_shared_list():
    shared = _shared_store.list()
    return jsonify({"ok":True,"shared":shared,"count":len(shared)})

@app.route("/api/shared/<share_id>/revoke", methods=["POST"])
def api_shared_revoke(share_id):
    ok = _shared_store.revoke(share_id)
    return jsonify({"ok":ok})

def _shared_latest_payload(shared_key: str, force: bool = False):
    share, error = _shared_record_for_key(shared_key)
    if error:
        return error
    try:
        view = _mailbox_service.shared_public_view(share, force=force)
        return jsonify({"ok":True, **view})
    except (IMAPNotConfigured, IMAPUnavailable):
        return jsonify({
            "ok":True,
            "mailbox":share.get("alias_email", ""),
            "label":"",
            "message":None,
            "fetched_at":datetime.now().isoformat(),
            "cache_age_sec":None,
            "note":"mailbox not ready",
        })
    except MailboxNotFound:
        return _shared_not_found_response()

def _shared_request_code():
    data = request.get_json(silent=True) or {}
    code = (
        data.get("redemption_code")
        or data.get("share_key")
        or request.args.get("redemption_code", "")
        or request.args.get("code", "")
        or ""
    )
    return str(code).strip()

def _shared_request_force():
    data = request.get_json(silent=True) or {}
    return bool(data.get("force")) or request.args.get("force", "0") == "1"

@app.route("/api/shared/latest", methods=["GET", "POST"])
def api_shared_latest_by_code():
    return _shared_latest_payload(_shared_request_code(), force=_shared_request_force())

@app.route("/api/shared/redeem", methods=["GET", "POST"])
def api_shared_redeem():
    return _shared_latest_payload(_shared_request_code(), force=_shared_request_force())

@app.route("/api/shared/<path:shared_key>/latest")
def api_shared_latest(shared_key):
    return _shared_latest_payload(shared_key, force=request.args.get("force", "0") == "1")

@app.route("/shared")
def shared_entry_page():
    return render_template("shared.html", shared_key="", invalid=False, shared_url=_shared_entry_url())

@app.route("/shared/<path:shared_key>")
def shared_page(shared_key):
    share = _shared_store.verify(shared_key)
    if not share:
        return render_template("shared.html", shared_key="", invalid=True, shared_url=_shared_entry_url()), 404
    return render_template("shared.html", shared_key=shared_key, invalid=False, shared_url=_shared_entry_url())

@app.route("/api/state")
def api_state():
    summary = _account_mgr.get_summary()
    with _lock:
        state = dict(_global_state); state.update(summary)
        state["cookies_ok"] = summary["active_accounts"] > 0
        state["alias_count"] = summary["total_aliases"]
        state["alias_active"] = summary["total_active_aliases"]
    return jsonify(state)

@app.route("/api/accounts")
def api_accounts():
    accounts = _account_mgr.list_accounts()
    safe = []
    for a in accounts:
        ac = _safe_account(a)
        ac["has_cookies"] = bool(a.get("cookies"))
        safe.append(ac)
    groups = _groups_with_mailbox_counts()
    imap_configs = _list_imap_configs_safe()
    return jsonify({"accounts":safe,"count":len(safe),"groups":groups,"imap_configs":imap_configs})
@app.route("/api/imap-configs")
def api_imap_configs():
    configs = _list_imap_configs_safe()
    return jsonify({"ok":True,"configs":configs,"count":len(configs)})

@app.route("/api/imap-configs", methods=["POST"])
def api_add_imap_config():
    data = request.get_json() or {}
    try:
        config = _account_mgr.add_imap_config(
            data.get("name", ""),
            data.get("email") or data.get("mail_email") or "",
            data.get("password") or data.get("mail_password") or "",
            data.get("host") or data.get("mail_host") or "",
            data.get("port") or data.get("mail_port") or 993,
        )
        return jsonify({"ok":True,"config":config})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400

@app.route("/api/imap-configs/<config_id>", methods=["PUT"])
def api_update_imap_config(config_id):
    data = request.get_json() or {}
    try:
        config = _account_mgr.update_imap_config(
            config_id,
            data.get("name", ""),
            data.get("email") or data.get("mail_email") or "",
            data.get("password") or data.get("mail_password") or "",
            data.get("host") or data.get("mail_host") or "",
            data.get("port") or data.get("mail_port") or 993,
        )
        return jsonify({"ok":True,"config":config})
    except KeyError as e:
        return jsonify({"ok":False,"error":str(e).strip("'")}), 404
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400

@app.route("/api/imap-configs/<config_id>", methods=["DELETE"])
def api_delete_imap_config(config_id):
    try:
        return jsonify({"ok":_account_mgr.delete_imap_config(config_id)})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400

@app.route("/api/imap-configs/<config_id>/test", methods=["GET", "POST"])
def api_test_imap_config(config_id):
    try:
        result = _account_mgr.test_imap_config(config_id)
        payload = {"ok":bool(result.get("ok")),"mail":result}
        if not payload["ok"]:
            payload["error"] = result.get("error") or "邮件读取暂不可用"
        return jsonify(payload)
    except KeyError as e:
        return jsonify({"ok":False,"error":str(e).strip("'")}), 404
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 400


@app.route("/api/groups")
def api_groups():
    groups = _groups_with_mailbox_counts()
    return jsonify({"ok":True,"groups":groups,"count":len(groups)})

@app.route("/api/groups", methods=["POST"])
def api_add_group():
    data = request.get_json() or {}
    group = _account_mgr.add_group(
        data.get("name", ""),
        data.get("description", ""),
        data.get("color", ""),
        _optional_int(data.get("sort_position")),
    )
    return jsonify({"ok":True,"group":group})

@app.route("/api/groups/reorder", methods=["POST", "PUT"])
def api_reorder_groups():
    data = request.get_json() or {}
    ok = _account_mgr.reorder_groups(data.get("group_ids", []))
    return jsonify({"ok":ok})

@app.route("/api/groups/<group_id>")
def api_group_detail(group_id):
    group = next((group for group in _groups_with_mailbox_counts() if group.get("id") == group_id), None)
    if not group:
        return jsonify({"ok":False,"error":"分组不存在"}), 404
    return jsonify({"ok":True,"group":group})

@app.route("/api/groups/<group_id>", methods=["PUT"])
def api_update_group(group_id):
    data = request.get_json() or {}
    group = _account_mgr.update_group(
        group_id,
        data.get("name", ""),
        data.get("description", ""),
        data.get("color", ""),
        _optional_int(data.get("sort_position")),
    )
    return jsonify({"ok":True,"group":group})

@app.route("/api/groups/<group_id>", methods=["DELETE"])
def api_delete_group(group_id):
    return jsonify({"ok":_account_mgr.delete_group(group_id)})

@app.route("/api/mailboxes/batch-group", methods=["POST"])
@app.route("/api/mailboxes/batch-update-group", methods=["POST"])
def api_mailboxes_batch_update_group():
    data = request.get_json() or {}
    aliases = data.get("alias_emails") or data.get("aliases") or data.get("mailboxes") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    moved = _account_mgr.move_mailboxes_to_group(aliases, data.get("group_id", ""))
    return jsonify({"ok":True,"moved":moved})

@app.route("/api/accounts/add", methods=["POST"])
def api_add_account():
    data = request.get_json() or {}
    name = data.get("name","未命名账号")
    cookie_input = data.get("cookie_input","")
    if not cookie_input: return jsonify({"ok":False,"error":"请提供 cookie_input"})
    try:
        account = _account_mgr.add_account(
            name,
            cookie_input,
            data.get("host", "icloud.com"),
            validate=False,
        )
        queued = _queue_account_validation(account["id"], "ui-add")
        account = _account_mgr.get_account(account["id"]) or account
        _emit_log("info",f"添加账号: {account.get('name','')}，后台校验已排队")
        safe = _safe_account(account)
        return jsonify({"ok":True,"queued":queued,"id":account["id"],"name":account["name"],"real_email":account.get("real_email",""),"alias_total":account.get("alias_total",0),"alias_active":account.get("alias_active",0),"status":account.get("status",""),"account":safe})
    except ValueError as e: return jsonify({"ok":False,"error":str(e)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/accounts/<acc_id>/session", methods=["GET"])
def api_get_account_session(acc_id):
    try:
        return jsonify({"ok":True,"account":_account_mgr.get_account_session(acc_id)})
    except KeyError as e:
        return jsonify({"ok":False,"error":str(e).strip("'")}), 404

@app.route("/api/accounts/<acc_id>/session", methods=["POST"])
def api_update_account_session(acc_id):
    data = request.get_json() or {}
    cookie_input = data.get("cookie_input", "")
    if not cookie_input:
        return jsonify({"ok":False,"error":"请提供 cookie_input"})
    try:
        account = _account_mgr.update_account_session(
            acc_id,
            data.get("name", "未命名账号"),
            cookie_input,
            data.get("host", "icloud.com"),
            validate=False,
        )
        queued = _queue_account_validation(account["id"], "ui-session")
        account = _account_mgr.get_account(account["id"]) or account
        _emit_log("info", f"更新账号会话: {account.get('name','')}，后台校验已排队")
        return jsonify({"ok":True,"queued":queued,"account":_safe_account(account)})
    except KeyError as e:
        return jsonify({"ok":False,"error":str(e).strip("'")}), 404
    except ValueError as e:
        return jsonify({"ok":False,"error":str(e)})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/accounts/<acc_id>/remove", methods=["POST"])
def api_remove_account(acc_id):
    ok = _account_mgr.remove_account(acc_id)
    return jsonify({"ok":ok})

@app.route("/api/accounts/<acc_id>/validate", methods=["POST"])
def api_validate_account(acc_id):
    try:
        queued = _queue_account_validation(acc_id, "ui-validate")
        account = _account_mgr.get_account(acc_id) or {}
        return jsonify({"ok":True,"queued":queued,"account":_safe_account(account),"real_email":account.get("real_email",""),"alias_total":account.get("alias_total",0)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/accounts/<acc_id>/mail-settings", methods=["POST"])
def api_set_mail_settings(acc_id):
    data = request.get_json() or {}
    try:
        account = _set_mail_settings_for_account(acc_id, data)
        result = _account_mgr.test_mail_connection(acc_id)
        return _mail_settings_response(account, result)
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/accounts/<acc_id>/mail-settings/test", methods=["GET", "POST"])
def api_test_mail_settings(acc_id):
    try:
        return _mail_test_response(acc_id)
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})


@app.route("/api/mail-settings/defaults")
def api_mail_settings_defaults():
    email = request.args.get("email", "")
    return jsonify({"ok":True,"host":infer_mail_host(email),"port":993})

@app.route("/api/accounts/<acc_id>/create", methods=["POST"])
def api_create_for_account(acc_id):
    data = request.get_json() or {}
    count = min(int(data.get("count",1)),50)
    label = data.get("label","")
    _update_state(creating=True)
    _emit_log("info",f"手动创建: 账号 {acc_id} x{count}")
    try:
        results = _account_mgr.create_aliases_for_account(acc_id, count, label)
        created = [r["email"] for r in results if r.get("ok")]
        errors = [r["error"] for r in results if not r.get("ok")]
        _update_state(creating=False)
        _increment_state(today_created=len(created), total_created=len(created))
        if created: _emit_log("success",f"创建完成: {len(created)} 个")
        return jsonify({"ok":len(created)>0,"emails":created,"created":len(created),"errors":len(errors),"error":errors[0] if errors else None})
    except Exception as e:
        _update_state(creating=False)
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/create-batch", methods=["POST"])
def api_create_batch():
    data = request.get_json() or {}
    account_ids = data.get("account_ids",[])
    count = min(int(data.get("count_per_account",5)),20)
    label = data.get("label","")
    interval = float(data.get("interval",3.0))
    if not account_ids: return jsonify({"ok":False,"error":"请选择至少一个账号"})
    _update_state(creating=True)
    _emit_log("info",f"批量创建: {len(account_ids)} 个账号 x{count}")
    try:
        all_results = _account_mgr.create_aliases_batch(account_ids, count, interval, label)
        total_created = sum(sum(1 for r in results if r.get("ok")) for results in all_results.values())
        total_errors = sum(sum(1 for r in results if not r.get("ok")) for results in all_results.values())
        _update_state(creating=False)
        _increment_state(today_created=total_created, total_created=total_created)
        _emit_log("success",f"批量完成: {total_created} 成功 / {total_errors} 失败")
        return jsonify({"ok":True,"total_created":total_created,"total_errors":total_errors,"results":{acc_id:[{"email":r.get("email"),"ok":r.get("ok"),"error":r.get("error")} for r in results] for acc_id,results in all_results.items()}})
    except Exception as e:
        _update_state(creating=False)
        return jsonify({"ok":False,"error":str(e)})


@app.route("/api/accounts/<acc_id>/inbox")
def api_inbox(acc_id):
    limit = request.args.get("limit",50,type=int)
    force = request.args.get("force","0")=="1"
    try:
        emails = _account_mgr.check_inbox(acc_id, limit=limit, force=force)
        stats = _account_mgr._cache.get_stats(acc_id)
        return jsonify({"emails":emails,"count":len(emails),"cached":stats})
    except Exception as e: return jsonify({"emails":[],"count":0,"error":str(e)})

@app.route("/api/accounts/<acc_id>/inbox-stream")
def api_inbox_stream(acc_id):
    limit = request.args.get("limit",50,type=int)
    days = request.args.get("days",7,type=int)
    def generate():
        yield f"data: {json.dumps({'type':'start'})}\n\n"
        try: mail = _account_mgr.get_mail_client(acc_id)
        except Exception as e: yield f"data: {json.dumps({'type':'error','error':str(e)[:200]})}\n\n"; return
        try:
            count = 0
            for msg in mail.stream_inbox(limit=limit, days=days):
                count += 1
                yield f"data: {json.dumps({'type':'email','count':count,'email':msg},ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type':'done','count':count})}\n\n"
        except GeneratorExit: pass
        except Exception as e: yield f"data: {json.dumps({'type':'error','error':str(e)[:200]})}\n\n"
        finally:
            try: mail.disconnect()
            except: pass
    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/accounts/<acc_id>/message/<msg_id>")
def api_message_body(acc_id, msg_id):
    try:
        mail = _account_mgr.get_mail_client(acc_id)
        try:
            full = mail.fetch_full(msg_id.encode() if isinstance(msg_id,str) else msg_id)
            return jsonify({"ok":True,"message":full})
        finally: mail.disconnect()
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/accounts/<acc_id>/mail/<alias_email>")
def api_specific_alias_mail(acc_id, alias_email):
    limit = request.args.get("limit",20,type=int)
    days = request.args.get("days",30,type=int)
    force = request.args.get("force","0")=="1"
    try:
        msgs = _account_mgr.check_alias_mail(acc_id, alias_email, limit=limit, days=days, force=force)
        return jsonify({"emails":msgs,"count":len(msgs),"alias":alias_email})
    except Exception as e: return jsonify({"emails":[],"count":0,"error":str(e)})

@app.route("/api/mail")
def api_mail_by_email():
    email = request.args.get("email","").strip().lower()
    alias = request.args.get("alias","").strip().lower()
    limit = request.args.get("limit",20,type=int)
    days = request.args.get("days",30,type=int)
    if not email: return jsonify({"error":"请提供 email 参数"})
    acc_id = None
    for a in _account_mgr.list_accounts():
        if a.get("icloud_email","").lower()==email or a.get("real_email","").lower()==email: acc_id=a["id"]; break
    if not acc_id: return jsonify({"error":f"未找到邮箱对应的账号: {email}"})
    try:
        if alias:
            msgs = _account_mgr.check_alias_mail(acc_id, alias, limit=limit, days=days)
            return jsonify({"emails":msgs,"count":len(msgs),"alias":alias,"account":email})
        else:
            by_alias = _account_mgr.check_all_aliases_mail(acc_id, limit_per=limit, days=days)
            total = sum(len(v) for v in by_alias.values())
            return jsonify({"by_alias":by_alias,"total":total,"account":email})
    except Exception as e: return jsonify({"error":str(e)})

@app.route("/api/accounts/<acc_id>/alias-mail")
def api_alias_mail(acc_id):
    force = request.args.get("force","0")=="1"
    try:
        by_alias = _account_mgr.check_all_aliases_mail(acc_id, force=force)
        total = sum(len(v) for v in by_alias.values())
        stats = _account_mgr._cache.get_stats(acc_id)
        return jsonify({"by_alias":by_alias,"total":total,"cached":stats})
    except Exception as e: return jsonify({"by_alias":{},"total":0,"error":str(e)})

@app.route("/api/aliases")
def api_aliases():
    try:
        aliases = _account_mgr.get_all_aliases()
        return jsonify({"aliases":aliases,"count":len(aliases)})
    except Exception as e: return jsonify({"aliases":[],"count":0,"error":str(e)})

@app.route("/api/emails")
def api_emails():
    limit = request.args.get("limit",0,type=int)
    emails = []
    f = RESULTS_DIR / "latest_emails.txt"
    if f.exists():
        lines = f.read_text(encoding="utf-8").strip().split("\n")
        if limit>0 and len(lines)>limit: lines = lines[-limit:]
        for line in lines:
            line = line.strip()
            if line and "@" in line:
                parts = line.split("\t")
                emails.append({"email":parts[0],"account_id":parts[1] if len(parts)>1 else "","created_at":""})
    emails.reverse()
    return jsonify({"emails":emails,"count":len(emails)})

def _start_scheduler_thread() -> bool:
    global _scheduler_thread, _stop_event
    if _scheduler_thread and _scheduler_thread.is_alive():
        _update_state(running=True)
        return False
    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    _update_state(running=True)
    return True


def _auto_start_scheduler_requested(args) -> bool:
    value = os.environ.get("AUTO_START_SCHEDULER", "").strip().lower()
    return bool(args.scheduler or value in ("1", "true", "yes", "on"))


@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    started = _start_scheduler_thread()
    return jsonify({"ok":True,"started":started})

@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    _stop_event.set()
    return jsonify({"ok":True})

@app.route("/api/logs")
def api_logs():
    limit = request.args.get("limit", 200, type=int)
    since = request.args.get("since", 0, type=int)
    entries = _log_entries(limit=limit, since=since)
    return jsonify({"ok":True,"logs":entries,"count":len(entries),"last_seq":entries[-1]["seq"] if entries else since})


@app.route("/api/log-stream")
def api_log_stream():
    def generate():
        while True:
            try: entry = _log_queue.get(timeout=30); yield f"data: {json.dumps(entry,ensure_ascii=False)}\n\n"
            except queue.Empty: yield ": heartbeat\n\n"
    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

def main():
    import argparse, os, signal as _signal
    parser = argparse.ArgumentParser(description="iCloud HME Web UI")
    parser.add_argument("--port",type=int,default=int(os.environ.get("PORT",5050)))
    parser.add_argument("--host",type=str,default=os.environ.get("HOST","0.0.0.0"))
    parser.add_argument("--scheduler",action="store_true",help="启动时自动运行调度器")
    parser.add_argument("--no-sync",action="store_true",help="跳过时间校准")
    args = parser.parse_args()
    if not args.no_sync:
        offset = _sync_time()
        if abs(offset)>0.5: print(f"[*] Time sync: offset {offset:.1f}s")
    threading.Thread(target=_health_loop, daemon=True).start()
    accounts = _account_mgr.list_accounts()
    if accounts:
        print(f"[+] {len(accounts)} account(s) loaded")
        for a in accounts: print(f"    [OK] {a.get('name','?')} - {a.get('real_email','?')} ({a.get('alias_total',0)} aliases)")
    else: print("[*] No accounts yet")
    if _auto_start_scheduler_requested(args):
        started = _start_scheduler_thread()
        print("[+] Scheduler auto-started" if started else "[+] Scheduler already running")
    def _shutdown(sig,frame): print("\n[*] Shutting down..."); _stop_event.set(); os._exit(0)
    _signal.signal(_signal.SIGINT, _shutdown)
    _signal.signal(_signal.SIGTERM, _shutdown)
    try:
        from waitress import serve
        print(f"\n  Production → http://{args.host}:{args.port}\n")
        serve(app, host=args.host, port=args.port, threads=8)
    except ImportError:
        print(f"\n  Dev server → http://{args.host}:{args.port}\n")
        app.run(host=args.host, port=args.port, debug=False, threaded=True)

if __name__=="__main__": main()
