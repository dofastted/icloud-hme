#!/usr/bin/env python3
"""遍历本地 HME 邮箱，对照 Apple 别名列表检测存活状态。

默认只读。加 --apply 后删除 inactive / missing / unverified：
有 anonymous_id 的先删 Apple 别名，再清本地索引；没有 id 的只清本地。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from account_manager import DEFAULT_GROUP_ID, AccountManager  # noqa: E402
from mailbox_service import MailboxService  # noqa: E402

RESULTS_DIR = HERE / "results"

STATUS_ALIVE = "alive"
STATUS_INACTIVE = "inactive"
STATUS_MISSING = "missing"
STATUS_UNVERIFIED = "unverified"
DELETE_STATUSES = {STATUS_INACTIVE, STATUS_MISSING, STATUS_UNVERIFIED}


def _alias_email(item: Dict) -> str:
    return str(
        item.get("email") or item.get("hme") or item.get("alias_email") or ""
    ).strip().lower()


def _anonymous_id(item: Dict) -> str:
    return str(item.get("anonymousId") or item.get("anonymous_id") or item.get("id") or "").strip()


def _is_active(item: Dict) -> bool:
    return bool(item.get("active", item.get("isActive", item.get("is_active", True))))


def parse_group_filter(value: str) -> Optional[Set[str]]:
    raw = str(value or "all").strip().lower()
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
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    out: Set[str] = set()
    for part in parts:
        key = part.lower()
        if key in mapping:
            out |= mapping[key]
            continue
        if key.startswith("grp_"):
            out.add(key)
            continue
        raise SystemExit(f"未知分组过滤: {value}")
    return out or None


def resolve_account(mgr: AccountManager, query: str) -> Dict:
    q = (query or "").strip().lower()
    if not q:
        raise SystemExit("必须指定 --account")
    accounts = mgr.list_accounts()
    for acc in accounts:
        if str(acc.get("id", "")).lower() == q:
            return acc
    for acc in accounts:
        fields = [acc.get("name"), acc.get("real_email"), acc.get("icloud_email"), acc.get("mail_email")]
        if any(str(v or "").strip().lower() == q for v in fields):
            return acc
    raise SystemExit(f"未找到账号: {query}")


def resolve_accounts(mgr: AccountManager, query: str) -> List[Dict]:
    if not str(query or "").strip():
        return list(mgr.list_accounts())
    return [resolve_account(mgr, query)]


def fetch_remote_aliases(mgr: AccountManager, acc_id: str) -> Tuple[List[Dict], Optional[str]]:
    try:
        client = mgr.get_client(acc_id, verbose=False)
        aliases = client.list_aliases()
    except Exception as exc:
        return [], str(exc) or type(exc).__name__
    if not isinstance(aliases, list):
        return [], "remote alias list is not a list"
    return aliases, None


def classify_mailbox(local: Dict, remote: Optional[Dict], account_error: Optional[str]) -> Dict:
    email = _alias_email(local)
    row = {
        "email": email,
        "account_id": local.get("account_id") or "",
        "account_name": local.get("account_name") or "",
        "group_id": local.get("group_id") or "",
        "group_name": local.get("group_name") or "",
        "is_active_local": bool(local.get("is_active", True)),
        "created_at": local.get("created_at") or "",
        "anonymous_id": "",
        "detail": "",
    }
    if account_error:
        row["status"] = STATUS_UNVERIFIED
        row["detail"] = account_error
        return row
    if remote is None:
        row["status"] = STATUS_MISSING
        row["detail"] = "Apple 别名列表中不存在"
        return row
    row["anonymous_id"] = _anonymous_id(remote)
    if _is_active(remote):
        row["status"] = STATUS_ALIVE
        return row
    row["status"] = STATUS_INACTIVE
    row["detail"] = "Apple 别名已停用"
    return row


def classify_account_mailboxes(
    local_items: List[Dict],
    remote_aliases: List[Dict],
    account_error: Optional[str],
) -> Tuple[List[Dict], List[Dict]]:
    remote_by_email: Dict[str, Dict] = {}
    for item in remote_aliases:
        email = _alias_email(item)
        if email:
            remote_by_email[email] = item

    rows = [
        classify_mailbox(local, remote_by_email.get(_alias_email(local)), account_error)
        for local in local_items
        if _alias_email(local)
    ]

    remote_only: List[Dict] = []
    if account_error:
        return rows, remote_only

    local_emails = {_alias_email(item) for item in local_items if _alias_email(item)}
    for email, item in remote_by_email.items():
        if email in local_emails:
            continue
        remote_only.append(
            {
                "email": email,
                "status": STATUS_ALIVE if _is_active(item) else STATUS_INACTIVE,
                "anonymous_id": _anonymous_id(item),
                "detail": "仅存在于 Apple 列表",
            }
        )
    remote_only.sort(key=lambda row: row.get("email") or "")
    return rows, remote_only


def _count_status(rows: List[Dict], status: str) -> int:
    return sum(1 for row in rows if row.get("status") == status)

def _purge_index(service: MailboxService, email: str) -> bool:
    path = service.index_path
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    mailboxes = data.get("mailboxes") if isinstance(data, dict) else None
    if not isinstance(mailboxes, dict) or email not in mailboxes:
        return False
    del mailboxes[email]
    data["updated_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _purge_latest_emails(service: MailboxService, email: str) -> bool:
    path = service.latest_emails_path
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    kept = []
    removed = False
    for line in lines:
        parts = line.strip().split("\t")
        if parts and _alias_email({"email": parts[0]}) == email:
            removed = True
            continue
        kept.append(line)
    if not removed:
        return False
    text = "\n".join(kept)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return True


def _purge_mailbox_group(mgr: AccountManager, email: str) -> bool:
    with mgr._lock:
        if email not in mgr.mailbox_groups:
            return False
        del mgr.mailbox_groups[email]
        mgr._save()
    return True


def purge_local_mailbox(mgr: AccountManager, service: MailboxService, email: str) -> List[str]:
    alias = _alias_email({"email": email})
    if not alias:
        return []
    touched = []
    if _purge_index(service, alias):
        touched.append("index")
    if _purge_latest_emails(service, alias):
        touched.append("latest_emails")
    if _purge_mailbox_group(mgr, alias):
        touched.append("group")
    return touched


def _short_text(text: str) -> str:
    raw = str(text or "")
    return raw.split("trustTokens", 1)[0].strip()[:180]


def _short_error(exc: Exception) -> str:
    return _short_text(str(exc) or type(exc).__name__)


def delete_mailbox(
    mgr: AccountManager,
    service: MailboxService,
    acc_id: str,
    row: Dict,
    client_cache: Dict,
) -> Dict:
    email = _alias_email(row)
    anon = str(row.get("anonymous_id") or "").strip()
    item = {
        "email": email,
        "account_id": acc_id,
        "status": row.get("status") or "",
        "anonymous_id": anon,
        "ok": False,
        "remote": "skipped",
        "local": [],
        "detail": "",
    }
    if anon:
        if "client" not in client_cache:
            try:
                client_cache["client"] = mgr.get_client(acc_id, verbose=False)
            except Exception as exc:
                item["detail"] = _short_error(exc)
                return item
        client = client_cache.get("client")
        if client is None:
            item["detail"] = "no iCloud client"
            return item
        try:
            client.delete(anon)
        except Exception as exc:
            item["detail"] = _short_error(exc)
            return item
        item["remote"] = "deleted"
    item["local"] = purge_local_mailbox(mgr, service, email)
    item["ok"] = True
    item["detail"] = "deleted"
    return item


def apply_account_deletes(
    mgr: AccountManager,
    service: MailboxService,
    acc_id: str,
    rows: List[Dict],
    apply: bool,
    interval: float,
) -> Dict:
    targets = [row for row in rows if row.get("status") in DELETE_STATUSES]
    deleted: List[Dict] = []
    failed: List[Dict] = []
    if not apply:
        return {"todo": len(targets), "deleted": deleted, "failed": failed}
    cache: Dict = {}
    remote_deleted = 0
    for idx, row in enumerate(targets, 1):
        item = delete_mailbox(mgr, service, acc_id, row, cache)
        if item.get("ok"):
            deleted.append(item)
            if item.get("remote") == "deleted":
                remote_deleted += 1
            print(f"[del {idx}/{len(targets)}] OK  {item['email']} {item['status']}", flush=True)
        else:
            failed.append(item)
            print(f"[del {idx}/{len(targets)}] FAIL {item['email']} {item['detail']}", flush=True)
        if interval > 0 and item.get("remote") == "deleted" and idx < len(targets):
            time.sleep(interval)
    if remote_deleted and cache.get("client") is not None:
        try:
            mgr._refresh_alias_counts(acc_id, cache["client"])
        except Exception:
            pass
    return {"todo": len(targets), "deleted": deleted, "failed": failed}



def process_account(
    mgr: AccountManager,
    service: MailboxService,
    account: Dict,
    allowed_groups: Optional[Set[str]],
    apply: bool = False,
    delete_interval: float = 0.35,
) -> Dict:
    acc_id = str(account.get("id") or "")
    local_items = service.list_mailboxes(account_id=acc_id)
    if allowed_groups is not None:
        local_items = [item for item in local_items if item.get("group_id") in allowed_groups]

    remote_aliases, account_error = fetch_remote_aliases(mgr, acc_id)
    rows, remote_only = classify_account_mailboxes(local_items, remote_aliases, account_error)
    rows.sort(key=lambda row: (row.get("status") or "", row.get("email") or ""))
    deletion = apply_account_deletes(mgr, service, acc_id, rows, apply, delete_interval)

    return {
        "account_id": acc_id,
        "account_name": account.get("name") or "",
        "account_status": account.get("status") or "",
        "error": _short_text(account_error) if account_error else None,
        "local_count": len(local_items),
        "remote_count": 0 if account_error else len(remote_aliases),
        "alive_count": _count_status(rows, STATUS_ALIVE),
        "inactive_count": _count_status(rows, STATUS_INACTIVE),
        "missing_count": _count_status(rows, STATUS_MISSING),
        "unverified_count": _count_status(rows, STATUS_UNVERIFIED),
        "remote_only_count": len(remote_only),
        "delete_todo": deletion["todo"],
        "deleted_count": len(deletion["deleted"]),
        "delete_failed_count": len(deletion["failed"]),
        "mailboxes": rows,
        "remote_only": remote_only,
        "deleted": deletion["deleted"],
        "delete_failed": deletion["failed"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="遍历 HME 邮箱并检测 Apple 侧存活状态")
    parser.add_argument("--account", default="", help="账号 id/name/email，空则全部账号")
    parser.add_argument("--group", default="all", help="可用/不可用/废弃/all，默认 all")
    parser.add_argument("--interval", type=float, default=0.4, help="账号间隔秒数")
    parser.add_argument("--delete-interval", type=float, default=0.35, help="删除间隔秒数")
    parser.add_argument("--apply", action="store_true", help="删除 inactive/missing/unverified")
    parser.add_argument("--out", default="", help="报告 JSON 路径")
    return parser.parse_args()


def _exit_code(summary: Dict) -> int:
    if int(summary.get("delete_failed_count") or 0):
        return 1
    if summary.get("mode") == "apply":
        return 0
    if int(summary.get("inactive_count") or 0) or int(summary.get("missing_count") or 0):
        return 1
    if int(summary.get("unverified_count") or 0):
        return 2
    return 0


def main() -> int:
    args = parse_args()
    allowed_groups = parse_group_filter(args.group)
    group_label = "all" if allowed_groups is None else ",".join(sorted(allowed_groups))
    mode = "apply" if args.apply else "dry-run"

    mgr = AccountManager()
    service = MailboxService(mgr)
    accounts = resolve_accounts(mgr, args.account)
    if not accounts:
        raise SystemExit("没有可检测的账号")

    print(f"accounts={len(accounts)} group={group_label} mode={mode}", flush=True)
    account_reports: List[Dict] = []
    for index, account in enumerate(accounts):
        report = process_account(
            mgr,
            service,
            account,
            allowed_groups,
            apply=bool(args.apply),
            delete_interval=float(args.delete_interval),
        )
        account_reports.append(report)
        print(
            f"[{index + 1}/{len(accounts)}] {report['account_name'] or report['account_id']} "
            f"local={report['local_count']} remote={report['remote_count']} "
            f"alive={report['alive_count']} inactive={report['inactive_count']} "
            f"missing={report['missing_count']} unverified={report['unverified_count']} "
            f"deleted={report['deleted_count']} failed={report['delete_failed_count']}"
            + (f" error={report['error']}" if report.get("error") else ""),
            flush=True,
        )
        if index < len(accounts) - 1 and args.interval > 0:
            time.sleep(args.interval)

    mailboxes = [row for report in account_reports for row in (report.get("mailboxes") or [])]
    deleted = [row for report in account_reports for row in (report.get("deleted") or [])]
    delete_failed = [row for report in account_reports for row in (report.get("delete_failed") or [])]
    summary = {
        "generated_at": datetime.now().isoformat(),
        "account_query": args.account,
        "group": group_label,
        "mode": mode,
        "account_count": len(account_reports),
        "local_count": sum(int(r.get("local_count") or 0) for r in account_reports),
        "alive_count": _count_status(mailboxes, STATUS_ALIVE),
        "inactive_count": _count_status(mailboxes, STATUS_INACTIVE),
        "missing_count": _count_status(mailboxes, STATUS_MISSING),
        "unverified_count": _count_status(mailboxes, STATUS_UNVERIFIED),
        "remote_only_count": sum(int(r.get("remote_only_count") or 0) for r in account_reports),
        "deleted_count": len(deleted),
        "delete_failed_count": len(delete_failed),
        "accounts": [
            {
                "account_id": r.get("account_id"),
                "account_name": r.get("account_name"),
                "error": r.get("error"),
                "local": r.get("local_count"),
                "remote": r.get("remote_count"),
                "alive": r.get("alive_count"),
                "inactive": r.get("inactive_count"),
                "missing": r.get("missing_count"),
                "unverified": r.get("unverified_count"),
                "deleted": r.get("deleted_count"),
                "delete_failed": r.get("delete_failed_count"),
            }
            for r in account_reports
        ],
        "inactive": [row for row in mailboxes if row.get("status") == STATUS_INACTIVE],
        "missing": [row for row in mailboxes if row.get("status") == STATUS_MISSING],
        "unverified": [row for row in mailboxes if row.get("status") == STATUS_UNVERIFIED],
        "deleted": deleted,
        "delete_failed": delete_failed,
        "account_reports": account_reports,
    }

    out_path = args.out.strip()
    if not out_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = str(RESULTS_DIR / f"mailbox_alive_{ts}.json")
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== TOTAL =====")
    print(
        f"mode={mode} alive={summary['alive_count']} inactive={summary['inactive_count']} "
        f"missing={summary['missing_count']} unverified={summary['unverified_count']} "
        f"deleted={summary['deleted_count']} failed={summary['delete_failed_count']}"
    )
    print(f"report={path}")
    return _exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
