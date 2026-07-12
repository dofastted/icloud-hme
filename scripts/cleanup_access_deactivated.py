#!/usr/bin/env python3
"""扫描指定账号可用 HME：拉最新 N 封邮件，命中 Access Deactivated 则删除。

默认 dry-run。加 --apply 才真正删除。
--live: 每个邮箱都走 IMAP 实时拉取（不全量依赖收件箱批扫）
--skip-verified: 跳过历史报告里已验证过的邮箱
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from account_manager import (  # noqa: E402
    DEFAULT_GROUP_ID,
    AccountManager,
)

KEYWORD = "access deactivated"
KEYWORD_RE = re.compile(r"access\s+deactivated", re.IGNORECASE)
RESULTS_DIR = HERE / "results"
MAILBOX_INDEX = RESULTS_DIR / "mailbox_index.json"
LATEST_EMAILS = RESULTS_DIR / "latest_emails.txt"


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _contains_keyword(text: str) -> bool:
    if not text:
        return False
    if KEYWORD_RE.search(text):
        return True
    return KEYWORD in _norm_text(text)


def _alias_email(item: Dict) -> str:
    return str(item.get("email") or item.get("hme") or item.get("alias_email") or "").strip().lower()


def _anonymous_id(item: Dict) -> str:
    return str(item.get("anonymousId") or item.get("anonymous_id") or item.get("id") or "").strip()


def _message_date_key(msg: Dict) -> str:
    return str(msg.get("date") or "")


def _recipient_blob(msg: Dict) -> str:
    return "\n".join(
        str(msg.get(key) or "")
        for key in ("to", "recipient_headers", "matched_recipient")
    ).lower()


def _message_hit(msg: Dict) -> bool:
    subject = str(msg.get("subject") or "")
    if _contains_keyword(subject):
        return True
    for key in ("body", "body_preview", "text"):
        if _contains_keyword(str(msg.get(key) or "")):
            return True
    return False


def resolve_account(mgr: AccountManager, query: str) -> Dict:
    q = (query or "").strip().lower()
    if not q:
        raise SystemExit("必须指定 --account")

    accounts = mgr.list_accounts()
    for acc in accounts:
        if str(acc.get("id", "")).lower() == q:
            return acc
    for acc in accounts:
        fields = [
            acc.get("name"),
            acc.get("real_email"),
            acc.get("icloud_email"),
            acc.get("mail_email"),
        ]
        if any(str(v or "").strip().lower() == q for v in fields):
            return acc
    raise SystemExit(f"未找到账号: {query}")


def load_verified_emails(paths: Iterable[Path]) -> Set[str]:
    verified: Set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("hits", "keeps", "errors", "deleted", "skipped"):
            rows = data.get(key) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                email = str(row.get("email") or "").strip().lower()
                if email:
                    verified.add(email)
    return verified


def discover_report_paths(explicit: List[str]) -> List[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    return sorted(RESULTS_DIR.glob("access_deactivated_cleanup_*.json"))


def _group_allowed(group_id: str, allowed: Optional[Set[str]]) -> bool:
    if not allowed:
        return True
    return group_id in allowed


def parse_group_filter(value: str) -> Optional[Set[str]]:
    raw = str(value or "available").strip().lower()
    if raw in {"all", "*"}:
        return None
    mapping = {
        "available": {DEFAULT_GROUP_ID},
        "default": {DEFAULT_GROUP_ID},
        "可用": {DEFAULT_GROUP_ID},
        "unavailable": {"grp_unavailable"},
        "不可用": {"grp_unavailable"},
        "deprecated": {"grp_deprecated"},
        "废弃": {"grp_deprecated"},
    }
    if raw in mapping:
        return set(mapping[raw])
    if raw.startswith("grp_"):
        return {raw}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: Set[str] = set()
    for part in parts:
        key = part.lower()
        if key in mapping:
            out |= mapping[key]
        elif key.startswith("grp_"):
            out.add(key)
        else:
            raise SystemExit(f"未知分组过滤: {value}")
    return out or {DEFAULT_GROUP_ID}


def list_aliases_remote(mgr: AccountManager, acc_id: str, allowed_groups: Optional[Set[str]]) -> List[Dict]:
    aliases = mgr.get_aliases_for_account(acc_id)
    out: List[Dict] = []
    for item in aliases:
        email = _alias_email(item)
        if not email:
            continue
        is_active = bool(item.get("active", item.get("isActive", True)))
        if not is_active:
            continue
        group = mgr.get_mailbox_group(email)
        group_id = group.get("id") if isinstance(group, dict) else str(group or DEFAULT_GROUP_ID)
        if not _group_allowed(group_id, allowed_groups):
            continue
        out.append(
            {
                "email": email,
                "anonymous_id": _anonymous_id(item),
                "label": str(item.get("label") or ""),
                "group_id": group_id,
                "source": "remote",
            }
        )
    out.sort(key=lambda x: x["email"])
    return out


def list_aliases_local(mgr: AccountManager, acc_id: str, allowed_groups: Optional[Set[str]]) -> List[Dict]:
    by_email: Dict[str, Dict] = {}

    if MAILBOX_INDEX.exists():
        try:
            data = json.loads(MAILBOX_INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        mailboxes = data.get("mailboxes", data)
        items: List[Dict] = []
        if isinstance(mailboxes, dict):
            items = [v for v in mailboxes.values() if isinstance(v, dict)]
        elif isinstance(mailboxes, list):
            items = [v for v in mailboxes if isinstance(v, dict)]
        for item in items:
            item_acc = str(item.get("account_id") or "")
            if item_acc and item_acc != acc_id:
                continue
            email = _alias_email(item)
            if not email:
                continue
            if item.get("is_active") is False:
                continue
            group = mgr.get_mailbox_group(email)
            group_id = group.get("id") if isinstance(group, dict) else str(group or DEFAULT_GROUP_ID)
            if not _group_allowed(group_id, allowed_groups):
                continue
            by_email[email] = {
                "email": email,
                "anonymous_id": _anonymous_id(item),
                "label": str(item.get("label") or ""),
                "group_id": group_id,
                "source": "index",
            }

    if LATEST_EMAILS.exists():
        try:
            lines = LATEST_EMAILS.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            parts = line.strip().split("\t")
            if not parts or "@" not in parts[0]:
                continue
            email = parts[0].strip().lower()
            line_acc = parts[1].strip() if len(parts) > 1 else ""
            if line_acc and line_acc != acc_id:
                continue
            if email in by_email:
                continue
            group = mgr.get_mailbox_group(email)
            group_id = group.get("id") if isinstance(group, dict) else str(group or DEFAULT_GROUP_ID)
            if not _group_allowed(group_id, allowed_groups):
                continue
            by_email[email] = {
                "email": email,
                "anonymous_id": "",
                "label": "",
                "group_id": group_id,
                "source": "latest_emails",
            }

    out = list(by_email.values())
    out.sort(key=lambda x: x["email"])
    return out


def list_target_aliases(
    mgr: AccountManager,
    acc_id: str,
    allowed_groups: Optional[Set[str]],
    allow_local_fallback: bool = True,
) -> Tuple[List[Dict], str]:
    # 若远端能列出任意别名，就以远端为准（再按分组过滤）
    remote_all = mgr.get_aliases_for_account(acc_id) or []
    if remote_all:
        out: List[Dict] = []
        for item in remote_all:
            email = _alias_email(item)
            if not email:
                continue
            is_active = bool(item.get("active", item.get("isActive", True)))
            if not is_active:
                continue
            group = mgr.get_mailbox_group(email)
            group_id = group.get("id") if isinstance(group, dict) else str(group or DEFAULT_GROUP_ID)
            if not _group_allowed(group_id, allowed_groups):
                continue
            out.append(
                {
                    "email": email,
                    "anonymous_id": _anonymous_id(item),
                    "label": str(item.get("label") or ""),
                    "group_id": group_id,
                    "source": "remote",
                }
            )
        out.sort(key=lambda x: x["email"])
        return out, "remote"
    if not allow_local_fallback:
        return [], "remote-empty"
    local = list_aliases_local(mgr, acc_id, allowed_groups)
    return local, "local-fallback"


def resolve_accounts(mgr: AccountManager, query: str) -> List[Dict]:
    q = (query or "").strip().lower()
    if q in {"all", "*"}:
        return list(mgr.list_accounts())
    return [resolve_account(mgr, query)]


def scan_recent_messages(mgr: AccountManager, acc_id: str, limit: int, days: int) -> List[Dict]:
    mail = mgr.get_mail_client(acc_id)
    try:
        return mail.check_inbox(limit=limit, days=days, include_junk=True)
    finally:
        try:
            mail.disconnect()
        except Exception:
            pass


def group_latest_by_alias(
    messages: List[Dict],
    aliases: List[Dict],
    per_alias: int,
) -> Dict[str, List[Dict]]:
    alias_set = {a["email"] for a in aliases}
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    ordered = sorted(messages, key=_message_date_key, reverse=True)
    for msg in ordered:
        blob = _recipient_blob(msg)
        if not blob:
            continue
        for email in alias_set:
            if email not in blob:
                continue
            bucket = grouped[email]
            if len(bucket) >= per_alias:
                continue
            mid = str(msg.get("id") or msg.get("message_id") or "")
            if mid and any(str(x.get("id") or x.get("message_id") or "") == mid for x in bucket):
                continue
            item = dict(msg)
            item["matched_recipient"] = email
            bucket.append(item)
    return grouped


def live_fetch_all(
    mgr: AccountManager,
    acc_id: str,
    aliases: List[Dict],
    per_alias: int,
    days: int,
) -> Dict[str, List[Dict]]:
    grouped: Dict[str, List[Dict]] = {}
    if not aliases:
        return grouped
    mail = mgr.get_mail_client(acc_id)
    try:
        total = len(aliases)
        for idx, alias in enumerate(aliases, 1):
            email = alias["email"]
            try:
                msgs = mail.find_by_recipient(email, limit=per_alias, days=days)
                grouped[email] = list(msgs or [])[:per_alias]
                hit_mark = "HIT" if any(_message_hit(m) for m in grouped[email]) else "ok"
                print(
                    f"[live {idx}/{total}] {email} -> {len(grouped[email])} msgs {hit_mark}",
                    flush=True,
                )
            except Exception as exc:
                alias["fetch_error"] = str(exc)
                grouped[email] = []
                print(f"[live {idx}/{total}] {email} ERR {exc}", flush=True)
            time.sleep(0.12)
    finally:
        try:
            mail.disconnect()
        except Exception:
            pass
    return grouped


def fill_missing_with_direct_fetch(
    mgr: AccountManager,
    acc_id: str,
    aliases: List[Dict],
    grouped: Dict[str, List[Dict]],
    per_alias: int,
    days: int,
) -> None:
    missing = [a for a in aliases if len(grouped.get(a["email"], [])) == 0]
    if not missing:
        return
    mail = mgr.get_mail_client(acc_id)
    try:
        for idx, alias in enumerate(missing, 1):
            email = alias["email"]
            try:
                msgs = mail.find_by_recipient(email, limit=per_alias, days=days)
            except Exception as exc:
                alias["fetch_error"] = str(exc)
                print(f"[fetch-miss {idx}/{len(missing)}] {email} ERR {exc}", flush=True)
                continue
            grouped[email] = list(msgs or [])[:per_alias]
            print(
                f"[fetch-miss {idx}/{len(missing)}] {email} -> {len(grouped[email])} msgs",
                flush=True,
            )
            time.sleep(0.12)
    finally:
        try:
            mail.disconnect()
        except Exception:
            pass


def maybe_fetch_bodies_for_candidates(
    mgr: AccountManager,
    acc_id: str,
    candidates: List[Tuple[Dict, List[Dict]]],
) -> None:
    need_ids: List[str] = []
    for _alias, msgs in candidates:
        for msg in msgs:
            if _message_hit(msg):
                continue
            mid = str(msg.get("id") or msg.get("message_id") or "")
            if mid and not msg.get("body") and not msg.get("body_preview"):
                need_ids.append(mid)
    if not need_ids:
        return

    mail = mgr.get_mail_client(acc_id)
    try:
        cache: Dict[str, Dict] = {}
        for mid in dict.fromkeys(need_ids):
            try:
                full = mail.fetch_full(mid.encode("utf-8"))
            except Exception:
                full = None
            if full:
                cache[mid] = full
        for _alias, msgs in candidates:
            for msg in msgs:
                mid = str(msg.get("id") or msg.get("message_id") or "")
                full = cache.get(mid)
                if not full:
                    continue
                if full.get("body"):
                    msg["body"] = full.get("body")
                if full.get("body_preview"):
                    msg["body_preview"] = full.get("body_preview")
                if full.get("subject") and not msg.get("subject"):
                    msg["subject"] = full.get("subject")
    finally:
        try:
            mail.disconnect()
        except Exception:
            pass


def delete_alias(mgr: AccountManager, acc_id: str, alias: Dict) -> Tuple[bool, str]:
    anonymous_id = alias.get("anonymous_id") or ""
    if not anonymous_id:
        return False, "missing anonymous_id"
    try:
        ok = mgr.delete_alias_for_account(acc_id, anonymous_id)
        return bool(ok), "deleted" if ok else "delete returned false"
    except Exception as exc:
        return False, str(exc)


def build_report_row(alias: Dict, msgs: List[Dict], hit: bool) -> Dict:
    subjects = [str(m.get("subject") or "") for m in msgs]
    return {
        "email": alias["email"],
        "anonymous_id": alias.get("anonymous_id") or "",
        "label": alias.get("label") or "",
        "source": alias.get("source") or "",
        "message_count": len(msgs),
        "hit": hit,
        "subjects": subjects,
        "fetch_error": alias.get("fetch_error") or "",
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="清理 Access Deactivated 的 HME")
    p.add_argument(
        "--account",
        default="all",
        help="账号 id / name / email，或 all 表示全部账号",
    )
    p.add_argument(
        "--group",
        default="all",
        help="分组过滤: available/unavailable/deprecated/all，或 grp_xxx",
    )
    p.add_argument("--per-alias", type=int, default=3, help="每个邮箱检查最新邮件数，默认 3")
    p.add_argument("--days", type=int, default=90, help="扫描天数，默认 90")
    p.add_argument("--inbox-limit", type=int, default=800, help="批扫收件箱上限（非 live），默认 800")
    p.add_argument(
        "--live",
        action="store_true",
        help="全量 live：每个邮箱单独 IMAP 拉最新 N 封",
    )
    p.add_argument(
        "--skip-verified",
        action="store_true",
        help="跳过历史报告中已验证邮箱",
    )
    p.add_argument(
        "--verified-from",
        action="append",
        default=[],
        help="指定已验证报告路径，可重复；默认读取 results/access_deactivated_cleanup_*.json",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="真正删除命中邮箱；默认 dry-run 只报告",
    )
    p.add_argument(
        "--out",
        default="",
        help="可选：写出 JSON 报告路径",
    )
    return p.parse_args()


def process_account(
    mgr: AccountManager,
    account: Dict,
    *,
    allowed_groups: Optional[Set[str]],
    group_label: str,
    per_alias: int,
    days: int,
    inbox_limit: int,
    live: bool,
    skip_verified: bool,
    verified: Set[str],
    apply: bool,
) -> Dict:
    acc_id = account["id"]
    print(
        f"\n===== account={acc_id} name={account.get('name')} "
        f"aliases={account.get('alias_total')}/{account.get('alias_active')} =====",
        flush=True,
    )

    aliases, source = list_target_aliases(mgr, acc_id, allowed_groups, allow_local_fallback=True)
    print(f"target_aliases={len(aliases)} source={source} group={group_label}", flush=True)

    skipped_rows: List[Dict] = []
    if skip_verified and verified:
        remain: List[Dict] = []
        for alias in aliases:
            if alias["email"] in verified:
                skipped_rows.append(
                    {
                        "email": alias["email"],
                        "anonymous_id": alias.get("anonymous_id") or "",
                        "label": alias.get("label") or "",
                        "reason": "already_verified",
                    }
                )
            else:
                remain.append(alias)
        print(f"skip_already_verified={len(skipped_rows)} todo={len(remain)}", flush=True)
        aliases = remain

    if not aliases:
        print("无待验证邮箱，跳过该账号", flush=True)
        return {
            "account_id": acc_id,
            "account_name": account.get("name"),
            "alias_source": source,
            "todo_aliases": 0,
            "skipped_count": len(skipped_rows),
            "hit_count": 0,
            "keep_count": 0,
            "error_count": 0,
            "deleted_count": 0,
            "delete_failed_count": 0,
            "hits": [],
            "keeps": [],
            "errors": [],
            "skipped": skipped_rows,
            "deleted": [],
            "delete_failed": [],
        }

    try:
        if live:
            print(f"live_fetch aliases={len(aliases)} per_alias={per_alias} days={days}", flush=True)
            grouped = live_fetch_all(mgr, acc_id, aliases, per_alias=per_alias, days=days)
        else:
            print(f"scan_inbox limit={inbox_limit} days={days} ...", flush=True)
            recent = scan_recent_messages(mgr, acc_id, limit=inbox_limit, days=days)
            print(f"scanned_messages={len(recent)}", flush=True)
            grouped = group_latest_by_alias(recent, aliases, per_alias=per_alias)
            covered = sum(1 for a in aliases if grouped.get(a["email"]))
            print(f"alias_with_mail_from_scan={covered}/{len(aliases)}", flush=True)
            fill_missing_with_direct_fetch(
                mgr, acc_id, aliases, grouped, per_alias=per_alias, days=days
            )
    except Exception as exc:
        print(f"account_fetch_error: {exc}", flush=True)
        errors = [
            {
                "email": a["email"],
                "anonymous_id": a.get("anonymous_id") or "",
                "label": a.get("label") or "",
                "source": a.get("source") or "",
                "message_count": 0,
                "hit": False,
                "subjects": [],
                "fetch_error": str(exc),
            }
            for a in aliases
        ]
        return {
            "account_id": acc_id,
            "account_name": account.get("name"),
            "alias_source": source,
            "todo_aliases": len(aliases),
            "skipped_count": len(skipped_rows),
            "hit_count": 0,
            "keep_count": 0,
            "error_count": len(errors),
            "deleted_count": 0,
            "delete_failed_count": 0,
            "hits": [],
            "keeps": [],
            "errors": errors,
            "skipped": skipped_rows,
            "deleted": [],
            "delete_failed": [],
        }

    preliminary: List[Tuple[Dict, List[Dict], bool]] = []
    for alias in aliases:
        msgs = grouped.get(alias["email"], [])[:per_alias]
        hit = any(_message_hit(m) for m in msgs)
        preliminary.append((alias, msgs, hit))

    need_body = [(a, m) for a, m, hit in preliminary if not hit and m]
    if need_body:
        print(f"fetch_bodies_for_non_subject_hits={len(need_body)}", flush=True)
        try:
            maybe_fetch_bodies_for_candidates(mgr, acc_id, need_body)
        except Exception as exc:
            print(f"body_fetch_error: {exc}", flush=True)

    hits: List[Dict] = []
    keeps: List[Dict] = []
    errors: List[Dict] = []

    for alias, msgs, _ in preliminary:
        hit = any(_message_hit(m) for m in msgs)
        row = build_report_row(alias, msgs, hit)
        row["account_id"] = acc_id
        if alias.get("fetch_error") and not msgs:
            errors.append(row)
            continue
        if hit:
            hits.append(row)
        else:
            keeps.append(row)

    print("\n=== HITS (Access Deactivated) ===")
    for row in hits:
        print(f"HIT  {row['email']}  msgs={row['message_count']}  {row['subjects'][:3]}")
    print(f"hit_count={len(hits)}")

    print("\n=== KEEP ===")
    for row in keeps:
        mark = "no-mail" if row["message_count"] == 0 else "ok"
        print(f"KEEP {row['email']}  {mark}  msgs={row['message_count']}")
    print(f"keep_count={len(keeps)}")

    if errors:
        print("\n=== ERRORS ===")
        for row in errors:
            print(f"ERR  {row['email']}  {row['fetch_error']}")
        print(f"error_count={len(errors)}")

    if skipped_rows:
        print(f"skipped_verified={len(skipped_rows)}")

    deleted = []
    delete_failed = []
    if apply and hits:
        print("\n=== APPLY DELETE ===", flush=True)
        for idx, row in enumerate(hits, 1):
            alias = {
                "email": row["email"],
                "anonymous_id": row["anonymous_id"],
            }
            ok, detail = delete_alias(mgr, acc_id, alias)
            item = {"email": row["email"], "account_id": acc_id, "ok": ok, "detail": detail}
            if ok:
                deleted.append(item)
                print(f"[del {idx}/{len(hits)}] OK  {row['email']}", flush=True)
            else:
                delete_failed.append(item)
                print(f"[del {idx}/{len(hits)}] FAIL {row['email']} {detail}", flush=True)
            time.sleep(0.35)
    elif hits:
        print("\nDRY-RUN: 未删除。确认后加 --apply 执行删除。")

    return {
        "account_id": acc_id,
        "account_name": account.get("name"),
        "alias_source": source,
        "todo_aliases": len(aliases),
        "skipped_count": len(skipped_rows),
        "hit_count": len(hits),
        "keep_count": len(keeps),
        "error_count": len(errors),
        "deleted_count": len(deleted),
        "delete_failed_count": len(delete_failed),
        "hits": hits,
        "keeps": keeps,
        "errors": errors,
        "skipped": skipped_rows,
        "deleted": deleted,
        "delete_failed": delete_failed,
    }


def main() -> int:
    args = parse_args()
    per_alias = max(1, min(int(args.per_alias or 3), 10))
    days = max(1, min(int(args.days or 90), 180))
    inbox_limit = max(50, min(int(args.inbox_limit or 800), 2000))
    allowed_groups = parse_group_filter(args.group)
    group_label = "all" if allowed_groups is None else ",".join(sorted(allowed_groups))

    mgr = AccountManager()
    accounts = resolve_accounts(mgr, args.account)
    verified: Set[str] = set()
    if args.skip_verified:
        report_paths = discover_report_paths(args.verified_from)
        verified = load_verified_emails(report_paths)
        print(f"verified_loaded={len(verified)} reports={len(report_paths)}", flush=True)

    print(
        f"accounts={len(accounts)} group={group_label} "
        f"mode={'APPLY' if args.apply else 'DRY-RUN'} "
        f"live={bool(args.live)} skip_verified={bool(args.skip_verified)}",
        flush=True,
    )

    account_reports: List[Dict] = []
    for account in accounts:
        # 已完整验证过的主账号在 group=all + skip 时仍会快速跳过
        report = process_account(
            mgr,
            account,
            allowed_groups=allowed_groups,
            group_label=group_label,
            per_alias=per_alias,
            days=days,
            inbox_limit=inbox_limit,
            live=bool(args.live),
            skip_verified=bool(args.skip_verified),
            verified=verified,
            apply=bool(args.apply),
        )
        account_reports.append(report)
        # 本轮结果也并入 verified，避免多账号重复邮箱交叉处理
        for key in ("hits", "keeps", "errors", "deleted"):
            for row in report.get(key) or []:
                email = str(row.get("email") or "").strip().lower()
                if email:
                    verified.add(email)

    hits = [row for r in account_reports for row in (r.get("hits") or [])]
    keeps = [row for r in account_reports for row in (r.get("keeps") or [])]
    errors = [row for r in account_reports for row in (r.get("errors") or [])]
    skipped = [row for r in account_reports for row in (r.get("skipped") or [])]
    deleted = [row for r in account_reports for row in (r.get("deleted") or [])]
    delete_failed = [row for r in account_reports for row in (r.get("delete_failed") or [])]

    summary = {
        "generated_at": datetime.now().isoformat(),
        "account_query": args.account,
        "accounts": [
            {
                "account_id": r.get("account_id"),
                "account_name": r.get("account_name"),
                "todo": r.get("todo_aliases"),
                "skipped": r.get("skipped_count"),
                "hits": r.get("hit_count"),
                "keeps": r.get("keep_count"),
                "errors": r.get("error_count"),
                "deleted": r.get("deleted_count"),
                "delete_failed": r.get("delete_failed_count"),
                "source": r.get("alias_source"),
            }
            for r in account_reports
        ],
        "mode": "apply" if args.apply else "dry-run",
        "live": bool(args.live),
        "skip_verified": bool(args.skip_verified),
        "group": group_label,
        "keyword": "Access Deactivated",
        "per_alias": per_alias,
        "days": days,
        "inbox_limit": inbox_limit,
        "todo_aliases": sum(int(r.get("todo_aliases") or 0) for r in account_reports),
        "skipped_count": len(skipped),
        "hit_count": len(hits),
        "keep_count": len(keeps),
        "error_count": len(errors),
        "deleted_count": len(deleted),
        "delete_failed_count": len(delete_failed),
        "hits": hits,
        "keeps": keeps,
        "errors": errors,
        "skipped": skipped,
        "deleted": deleted,
        "delete_failed": delete_failed,
        "account_reports": account_reports,
    }

    out_path = args.out.strip()
    if not out_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = str(RESULTS_DIR / f"access_deactivated_cleanup_{ts}.json")
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== TOTAL =====")
    for row in summary["accounts"]:
        print(
            f"{row['account_name']}: todo={row['todo']} skipped={row['skipped']} "
            f"hits={row['hits']} keeps={row['keeps']} errors={row['errors']} "
            f"deleted={row['deleted']} failed={row['delete_failed']}"
        )
    print(f"report={path}")
    print(
        f"summary todo={summary['todo_aliases']} skipped={summary['skipped_count']} "
        f"hits={summary['hit_count']} keeps={summary['keep_count']} "
        f"errors={summary['error_count']} deleted={summary['deleted_count']} "
        f"delete_failed={summary['delete_failed_count']}"
    )
    return 0 if not delete_failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
