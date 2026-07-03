#!/usr/bin/env python3
"""iCloud HME Web UI — 多账号聚合管理平台 — Flask single-page app."""
import sys, os, json, time, queue, secrets, threading
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0, str(HERE))

from flask import Flask, Response, request, jsonify, render_template, g
from icloud_hme import ICloudHME, extract_chrome_cookies
from account_manager import AccountManager
from api_keys import APIKeyStore, extract_api_key
from mailbox_service import IMAPNotConfigured, IMAPUnavailable, MailboxNotFound, MailboxService
from shared_mailboxes import SharedMailboxStore

# ---- config ----
RESULTS_DIR = HERE / "results"
LOGS_DIR = HERE / "logs"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
_log_queue = queue.Queue()
_today_key = datetime.now().strftime("%Y%m%d")
_global_state = {"running":False,"creating":False,"round_status":"","total_created":0,"today_created":0,"current_round_created":0,"next_trigger":None,"last_error":None,"cookies_ok":False,"alias_count":0,"alias_active":0}
_lock = threading.Lock()
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
    return {k:v for k,v in account.items() if k not in ("cookies","app_password")}

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

def _share_url(raw_key: str) -> str:
    return request.url_root.rstrip("/") + "/shared/" + raw_key

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
                _time_offset = (net_time - datetime.now()).total_seconds()
                return _time_offset
        except: continue
    return 0.0

def _now() -> datetime: return datetime.now() + timedelta(seconds=_time_offset)

def _emit_log(level, msg): _log_queue.put({"time":_now().strftime("%H:%M:%S"),"level":level,"msg":msg})

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

def _scheduler_loop():
    """后台调度器：北京时间 7:00-20:00，随机间隔 60-90min，每账号随机 3-5 个。"""
    import random as _random
    from icloud_hme import ICloudHME
    _update_state(running=True, round_status="等待触发窗口")
    _emit_log("info", "调度器已启动 (BJ 7-20h, 间隔 60-90min, 每轮 3-5 个)")
    def _bj_hour() -> int: return (_now().hour + 8) % 24
    while not _stop_event.is_set():
        h = _bj_hour()
        if h < 7 or h >= 20: _update_state(round_status=f"非窗口时段 (BJ {h}:00)，等待..."); _stop_event.wait(1800); continue
        active_accounts = [a for a in _account_mgr.list_accounts() if a.get("status") == "active"]
        if not active_accounts: _update_state(creating=False, round_status="无活跃账号，跳过"); _stop_event.wait(1800); continue
        round_total = 0
        for i, account in enumerate(active_accounts):
            if _stop_event.is_set(): break
            acc_id = account["id"]; acc_name = account.get("name", acc_id)
            target_count = _random.randint(3, 5)
            _emit_log("info", f"[{acc_name}] 本轮目标 {target_count} 个")
            client = ICloudHME(account["cookies"], host=account.get("host","icloud.com"), verbose=False)
            created = 0; errors = 0
            while created < target_count and errors < 3 and not _stop_event.is_set():
                try:
                    result = client.create_alias(label=f"{acc_name} {_now().strftime('%m%d%H%M')}", max_retries=2)
                    email = result.get("email","")
                    if email:
                        created += 1; round_total += 1
                        _emit_log("success", f"[{acc_name}] ({created}/{target_count}) {email}")
                        _increment_state(today_created=1, total_created=1)
                        with open(str(RESULTS_DIR/"latest_emails.txt"),"a",encoding="utf-8") as f: f.write(f"{email}\t{acc_id}\n")
                        _account_mgr.update_account(acc_id, alias_total=account.get("alias_total",0)+1)
                        account["alias_total"] = account.get("alias_total",0)+1
                        errors = 0; time.sleep(_random.uniform(15,45))
                    else: errors += 1
                except Exception as e:
                    err_str = str(e)
                    if _is_limit_error(err_str): _emit_log("info",f"[{acc_name}] 触达上限: {err_str[:60]}"); break
                    errors += 1; _emit_log("warn",f"[{acc_name}] 失败: {err_str[:80]}")
            if i < len(active_accounts)-1: time.sleep(_random.uniform(120,300))
        _update_state(creating=False, current_round_created=round_total, round_status=f"本轮创建 {round_total} 个")
        interval_sec = _random.randint(3600,5400)
        target = _now() + timedelta(seconds=interval_sec)
        _update_state(next_trigger=target.timestamp())
        _emit_log("info", f"下轮 {target.strftime('%H:%M')} (间隔 {interval_sec//60}min)")
        _stop_event.wait(interval_sec)
    _update_state(running=False, next_trigger=None, round_status="已停止")
    _emit_log("info", "调度器已停止")

def _health_loop():
    _error_reported = set()
    while not _stop_event.is_set():
        if _stop_event.wait(300): break
        for account in _account_mgr.list_accounts():
            if account.get("status") != "active": continue
            try: _account_mgr.validate_account(account["id"]); _error_reported.discard(account["id"])
            except Exception as e:
                if account["id"] not in _error_reported: _emit_log("warn",f"健康检查失败 [{account.get('name','?')}]: {str(e)[:100]}"); _error_reported.add(account["id"])

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
        item["has_app_password"] = bool(account.get("app_password"))
        accounts.append(item)
    return jsonify({"ok":True,"accounts":accounts,"count":len(accounts)})

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
    )
    return jsonify({"ok":True,"account":_safe_account(account)})

@app.route("/api/v1/accounts/<acc_id>/session/validate", methods=["POST"])
@_require_api_key
def api_v1_validate_session(acc_id):
    account = _account_mgr.validate_account(acc_id)
    return jsonify({"ok":account.get("status")=="active","account":_safe_account(account)})

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

@app.route("/api/v1/accounts/<acc_id>/aliases/<anonymous_id>", methods=["DELETE"])
@_require_api_key
def api_v1_delete_alias(acc_id, anonymous_id):
    return jsonify({"ok":_account_mgr.delete_alias_for_account(acc_id, anonymous_id)})

@app.route("/api/v1/accounts/<acc_id>/imap", methods=["POST"])
@_require_api_key
def api_v1_set_imap(acc_id):
    data = request.get_json() or {}
    app_password = data.get("app_password", "").strip()
    icloud_email = data.get("icloud_email", "").strip()
    if not app_password or not icloud_email:
        return jsonify({"ok":False,"error":"icloud_email and app_password are required"}), 400
    _account_mgr.set_app_password(acc_id, app_password, icloud_email)
    return jsonify(_account_mgr.test_imap_connection(acc_id))

@app.route("/api/v1/accounts/<acc_id>/verification-codes")
@_require_api_key
def api_v1_verification_codes(acc_id):
    alias = request.args.get("alias", "").strip()
    limit = min(request.args.get("limit", 10, type=int), 50)
    days = min(request.args.get("days", 1, type=int), 30)
    codes = _account_mgr.get_verification_codes(acc_id, alias, limit=limit, days=days)
    return jsonify({"ok":True,"codes":codes,"count":len(codes),"alias":alias})

def _mailbox_list_payload():
    items = _mailbox_service.list_mailboxes(
        q=request.args.get("q", ""),
        account_id=request.args.get("account_id", "") or request.args.get("account", ""),
        status=request.args.get("status", ""),
    )
    total = len(items)
    page, limit, offset = _paginate(items)
    return {"ok":True,"mailboxes":page,"count":len(page),"total":total,"limit":limit,"offset":offset}

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
    return jsonify({"ok":True,"shared":created,"share_key":raw_key,"share_url":_share_url(raw_key)})

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
    return jsonify({"ok":True,"shared":created,"share_key":raw_key,"share_url":_share_url(raw_key)})

@app.route("/api/shared")
def api_shared_list():
    shared = _shared_store.list()
    return jsonify({"ok":True,"shared":shared,"count":len(shared)})

@app.route("/api/shared/<share_id>/revoke", methods=["POST"])
def api_shared_revoke(share_id):
    ok = _shared_store.revoke(share_id)
    return jsonify({"ok":ok})

@app.route("/api/shared/<path:shared_key>/latest")
def api_shared_latest(shared_key):
    share, error = _shared_record_for_key(shared_key)
    if error:
        return error
    try:
        view = _mailbox_service.shared_public_view(share, force=False)
        return jsonify({"ok":True, **view})
    except IMAPNotConfigured:
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

@app.route("/shared/<path:shared_key>")
def shared_page(shared_key):
    share = _shared_store.verify(shared_key)
    if not share:
        return render_template("shared.html", shared_key="", invalid=True), 404
    return render_template("shared.html", shared_key=shared_key, invalid=False)

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
        ac = {k:v for k,v in a.items() if k!="cookies"}
        ac["has_cookies"] = bool(a.get("cookies"))
        ac["has_app_password"] = bool(a.get("app_password"))
        safe.append(ac)
    return jsonify({"accounts":safe,"count":len(safe)})

@app.route("/api/accounts/add", methods=["POST"])
def api_add_account():
    data = request.get_json() or {}
    name = data.get("name","未命名账号")
    cookie_input = data.get("cookie_input","")
    if not cookie_input: return jsonify({"ok":False,"error":"请提供 cookie_input"})
    try:
        account = _account_mgr.add_account(name, cookie_input)
        _emit_log("info",f"添加账号: {account.get('name','')} ({account.get('real_email','?')})")
        return jsonify({"ok":True,"id":account["id"],"name":account["name"],"real_email":account.get("real_email",""),"alias_total":account.get("alias_total",0),"alias_active":account.get("alias_active",0),"status":account.get("status","")})
    except ValueError as e: return jsonify({"ok":False,"error":str(e)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/accounts/<acc_id>/remove", methods=["POST"])
def api_remove_account(acc_id):
    ok = _account_mgr.remove_account(acc_id)
    return jsonify({"ok":ok})

@app.route("/api/accounts/<acc_id>/validate", methods=["POST"])
def api_validate_account(acc_id):
    try:
        account = _account_mgr.validate_account(acc_id)
        return jsonify({"ok":True,"real_email":account.get("real_email",""),"alias_total":account.get("alias_total",0)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

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

@app.route("/api/accounts/<acc_id>/app-password", methods=["POST"])
def api_set_app_password(acc_id):
    data = request.get_json() or {}
    pwd = data.get("app_password","").strip()
    icloud_email = data.get("icloud_email","").strip()
    if not pwd: return jsonify({"ok":False,"error":"密码不能为空"})
    try:
        _account_mgr.set_app_password(acc_id, pwd)
        if icloud_email: _account_mgr.update_account(acc_id, icloud_email=icloud_email)
        result = _account_mgr.test_imap_connection(acc_id)
        return jsonify(result)
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

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
    try:
        msgs = _account_mgr.check_alias_mail(acc_id, alias_email, limit=limit, days=days)
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

@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    global _scheduler_thread, _stop_event
    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    _update_state(running=True)
    return jsonify({"ok":True})

@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    _stop_event.set()
    return jsonify({"ok":True})

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
    if args.scheduler:
        global _scheduler_thread, _stop_event
        _stop_event.clear()
        _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
        _scheduler_thread.start()
        _update_state(running=True)
        print("[+] Scheduler auto-started")
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
