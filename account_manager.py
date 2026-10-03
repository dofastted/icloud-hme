#!/usr/bin/env python3
"""
iCloud HME — 多账号管理器
===========================
管理多组 iCloud 账号及其隐私邮箱别名。

功能:
  - 账号 CRUD (增删改查)
  - Cookie 导入解析 (Header String / JSON)
  - 批量会话校验
  - 别名按账号归属索引
  - 跨账号并发/轮询创建

用法:
    from account_manager import AccountManager

    mgr = AccountManager()
    mgr.add_account("主号", cookie_header_string)
    mgr.create_aliases_batch(["acc_xxx", "acc_yyy"], count_per_account=5)
"""

import os
import json
import time
import uuid
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("HME_DATA_DIR", str(HERE)))
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
OLD_COOKIES_FILE = DATA_DIR / "cookies.json"
RESULTS_DIR = DATA_DIR / "results"
LATEST_EMAILS = RESULTS_DIR / "latest_emails.txt"
SCHEDULER_ALIAS_LIMIT = int(os.environ.get("HME_SCHEDULER_ALIAS_LIMIT", "750"))
SCHEDULER_REFRESH_MARGIN = int(os.environ.get("HME_SCHEDULER_REFRESH_MARGIN", "5"))
CREATE_INTERVAL_SEC = float(os.environ.get("HME_CREATE_INTERVAL_SEC", "20"))
DEFAULT_GROUP_ID = "grp_default"
DEFAULT_GROUP_NAME = "可用"
DEFAULT_GROUP_COLOR = "#1f8b4c"
UNAVAILABLE_GROUP_ID = "grp_unavailable"
DEPRECATED_GROUP_ID = "grp_deprecated"
CLAUDE_GROUP_ID = "grp_claude"
OPENAI_GROUP_ID = "grp_openai"
BUILTIN_GROUP_DEFINITIONS = (
    {
        "id": DEFAULT_GROUP_ID,
        "name": DEFAULT_GROUP_NAME,
        "description": "可继续使用的 HME 邮箱",
        "color": DEFAULT_GROUP_COLOR,
        "sort_order": 0,
        "is_default": True,
    },
    {
        "id": UNAVAILABLE_GROUP_ID,
        "name": "不可用",
        "description": "暂时无法使用或需要检查的 HME 邮箱",
        "color": "#d97706",
        "sort_order": 1,
        "is_default": False,
    },
    {
        "id": DEPRECATED_GROUP_ID,
        "name": "废弃",
        "description": "不再使用的 HME 邮箱",
        "color": "#6b7280",
        "sort_order": 2,
        "is_default": False,
    },
    {
        "id": CLAUDE_GROUP_ID,
        "name": "Claude",
        "description": "用于 Claude 的 HME 邮箱",
        "color": "#8b4513",
        "sort_order": 3,
        "is_default": False,
    },
    {
        "id": OPENAI_GROUP_ID,
        "name": "OpenAI",
        "description": "用于 OpenAI 的 HME 邮箱",
        "color": "#147a43",
        "sort_order": 4,
        "is_default": False,
    },
)
BUILTIN_GROUP_IDS = {group["id"] for group in BUILTIN_GROUP_DEFINITIONS}
MAIL_PROVIDER_HOSTS = {
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
    "qq.com": "imap.qq.com",
    "vip.qq.com": "imap.qq.com",
    "163.com": "imap.163.com",
    "126.com": "imap.126.com",
    "yeah.net": "imap.yeah.net",
    "gmail.com": "imap.gmail.com",
    "outlook.com": "imap-mail.outlook.com",
    "hotmail.com": "imap-mail.outlook.com",
    "live.com": "imap-mail.outlook.com",
}

def normalize_group_color(color: str) -> str:
    value = str(color or "").strip()
    if len(value) in (4, 7) and value.startswith("#"):
        digits = value[1:]
        if all(ch in "0123456789abcdefABCDEF" for ch in digits):
            return "#" + digits.lower()
    return DEFAULT_GROUP_COLOR


def infer_mail_host(email: str) -> str:
    domain = str(email or "").strip().lower().rsplit("@", 1)[-1]
    return MAIL_PROVIDER_HOSTS.get(domain, "")


def account_mail_email(account: Dict) -> str:
    return (
        str(account.get("mail_email") or "").strip()
        or str(account.get("real_email") or "").strip()
        or str(account.get("icloud_email") or "").strip()
    )


def account_mail_host(account: Dict) -> str:
    return str(account.get("mail_host") or "").strip() or infer_mail_host(account_mail_email(account))


def account_has_mail_config(account: Dict) -> bool:
    return bool(account_mail_email(account) and account_mail_host(account) and (account.get("mail_password") or account.get("app_password")))


def normalize_account_host(host: Any = "") -> str:
    """Map UI region aliases (apple.com / apple.com.cn) to icloud hosts."""
    raw = str(host or "").strip() or "icloud.com"
    try:
        from icloud_hme import normalize_icloud_host

        return normalize_icloud_host(raw)
    except Exception:
        # Tests often replace icloud_hme with a stub; keep a local fallback.
        h = raw.lower()
        if "apple.com.cn" in h or h.endswith("icloud.com.cn") or h == "icloud.com.cn":
            return "icloud.com.cn"
        return "icloud.com"


def is_hard_session_error(error: Any) -> bool:
    """True only for auth/session-invalid failures that should demote status.

    Network blips, proxy cold-start, and Apple temporary 5xx must not flip a
    previously active account to error after restart or health checks.
    """
    text = str(error or "").strip().lower()
    if not text:
        return False
    hard_markers = (
        "http 401",
        "http 403",
        "http 421",
        "accountloginui",
        "authentication",
        "not authenticated",
        "invalid session",
        "session expired",
        "sign in",
        "signin",
        "login required",
        "require login",
        "hsa challenge",
        "two-factor",
        "2fa",
        "cookie",
        "无法解析 cookie",
        "该会话属于已存在账号",
    )
    if any(marker in text for marker in hard_markers):
        return True
    soft_markers = (
        "timeout",
        "timed out",
        "连接失败",
        "connection",
        "temporarily",
        "proxy",
        "name or service not known",
        "temporary failure",
        "network is unreachable",
        "http 5",
        "http 429",
        "max retries",
    )
    if any(marker in text for marker in soft_markers):
        return False
    # Unknown errors: treat as soft for already-active accounts.
    return False


def coerce_mail_port(port: Any) -> int:
    try:
        value = int(port or 993)
    except (TypeError, ValueError) as exc:
        raise ValueError("端口必须是数字") from exc
    if value < 1 or value > 65535:
        raise ValueError("端口必须在 1-65535 之间")
    return value


def account_alias_total(account: Dict) -> int:
    try:
        return int(account.get("alias_total") or 0)
    except (TypeError, ValueError):
        return 0


def account_reached_scheduler_limit(account: Dict, limit: int = SCHEDULER_ALIAS_LIMIT) -> bool:
    return account_alias_total(account) >= limit


def scheduler_count_needs_refresh(
    account: Dict,
    limit: int = SCHEDULER_ALIAS_LIMIT,
    margin: int = SCHEDULER_REFRESH_MARGIN,
) -> bool:
    total = account_alias_total(account)
    if total < 0:
        return True
    return 0 < limit - total <= margin


def scheduler_eligible_accounts(accounts: List[Dict], limit: int = SCHEDULER_ALIAS_LIMIT) -> List[Dict]:
    return [
        account for account in accounts
        if account.get("status") == "active" and not account_reached_scheduler_limit(account, limit)
    ]

from mail_cache import get_cache  # noqa: E402


class AccountManager:
    """多账号管理器"""

    def __init__(self):
        self.accounts: Dict[str, Dict] = {}
        self.groups: Dict[str, Dict] = {}
        self.mailbox_groups: Dict[str, str] = {}
        self.imap_configs: Dict[str, Dict] = {}
        self._lock = threading.RLock()
        self._cache = get_cache()
        self._load()

    def _load(self):
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        if OLD_COOKIES_FILE.exists() and not ACCOUNTS_FILE.exists():
            try:
                self._migrate_old_cookies()
            except Exception:
                pass

        if ACCOUNTS_FILE.exists():
            try:
                data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
                self.accounts = data.get("accounts", {})
                groups = data.get("groups", {})
                if isinstance(groups, list):
                    self.groups = {
                        str(g.get("id")): g for g in groups
                        if isinstance(g, dict) and g.get("id")
                    }
                elif isinstance(groups, dict):
                    self.groups = groups
                mailbox_groups = data.get("mailbox_groups", {})
                self.mailbox_groups = mailbox_groups if isinstance(mailbox_groups, dict) else {}
                imap_configs = data.get("imap_configs", {})
                self.imap_configs = imap_configs if isinstance(imap_configs, dict) else {}
            except (json.JSONDecodeError, OSError):
                self.accounts = {}
                self.groups = {}
                self.mailbox_groups = {}
                self.imap_configs = {}
        changed = self._normalize_loaded_state()
        if changed:
            self._save()

    def _save(self):
        with self._lock:
            self._normalize_loaded_state()
            ACCOUNTS_FILE.write_text(
                json.dumps({
                    "accounts": self.accounts,
                    "groups": self.groups,
                    "mailbox_groups": self.mailbox_groups,
                    "imap_configs": self.imap_configs,
                    "updated_at": datetime.now().isoformat(),
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def _migrate_old_cookies(self):
        try:
            old = json.loads(OLD_COOKIES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(old, dict) or not old:
            return

        acc_id = self._generate_id()
        self.accounts[acc_id] = {
            "id": acc_id,
            "name": "默认账号",
            "real_email": "",
            "cookies": old,
            "host": "icloud.com",
            "status": "active",
            "alias_total": 0,
            "alias_active": 0,
            "last_validated": None,
            "last_error": None,
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        try:
            OLD_COOKIES_FILE.rename(OLD_COOKIES_FILE.with_suffix(".json.bak"))
        except OSError:
            pass

    def _generate_id(self) -> str:
        return "acc_" + uuid.uuid4().hex[:8]

    def _generate_group_id(self) -> str:
        return "grp_" + uuid.uuid4().hex[:8]

    def _generate_imap_config_id(self) -> str:
        return "imap_" + uuid.uuid4().hex[:8]

    @staticmethod
    def _builtin_group(group_id: str) -> Dict[str, Any]:
        for group in BUILTIN_GROUP_DEFINITIONS:
            if group["id"] == group_id:
                item = dict(group)
                item["is_system"] = True
                item["created_at"] = datetime.now().isoformat()
                return item
        raise KeyError(group_id)

    @staticmethod
    def _default_group() -> Dict[str, Any]:
        return AccountManager._builtin_group(DEFAULT_GROUP_ID)

    def _normalize_loaded_state(self) -> bool:
        changed = False
        if not isinstance(self.groups, dict):
            self.groups = {}
            changed = True

        for definition in BUILTIN_GROUP_DEFINITIONS:
            gid = definition["id"]
            if gid not in self.groups or not isinstance(self.groups.get(gid), dict):
                self.groups[gid] = self._builtin_group(gid)
                changed = True

        for gid, group in list(self.groups.items()):
            if not isinstance(group, dict):
                del self.groups[gid]
                changed = True
                continue
            normalized_id = str(group.get("id") or gid).strip()
            if normalized_id != gid:
                del self.groups[gid]
                gid = normalized_id or self._generate_group_id()
                self.groups[gid] = group
                changed = True
            if group.get("id") != gid:
                group["id"] = gid
                changed = True

            definition = next((item for item in BUILTIN_GROUP_DEFINITIONS if item["id"] == gid), None)
            if definition:
                for key in ("name", "description", "color", "sort_order", "is_default"):
                    if group.get(key) != definition[key]:
                        group[key] = definition[key]
                        changed = True
                if group.get("is_system") is not True:
                    group["is_system"] = True
                    changed = True
            else:
                if not str(group.get("name") or "").strip():
                    group["name"] = gid
                    changed = True
                normalized_color = normalize_group_color(group.get("color"))
                if group.get("color") != normalized_color:
                    group["color"] = normalized_color
                    changed = True
                if group.get("is_default") is not False:
                    group["is_default"] = False
                    changed = True
                if group.get("is_system") is not False:
                    group["is_system"] = False
                    changed = True
                try:
                    group["sort_order"] = int(group.get("sort_order") or 0)
                except (TypeError, ValueError):
                    group["sort_order"] = 0
                    changed = True

            if "description" not in group:
                group["description"] = ""
                changed = True
            if "created_at" not in group:
                group["created_at"] = datetime.now().isoformat()
                changed = True

        for definition in BUILTIN_GROUP_DEFINITIONS:
            gid = definition["id"]
            if gid not in self.groups:
                self.groups[gid] = self._builtin_group(gid)
                changed = True

        if not isinstance(self.mailbox_groups, dict):
            self.mailbox_groups = {}
            changed = True

        for raw_alias, raw_group_id in list(self.mailbox_groups.items()):
            alias = self._normalize_mailbox_email(raw_alias)
            gid = str(raw_group_id or "").strip()
            if not alias or gid not in self.groups or gid == DEFAULT_GROUP_ID:
                del self.mailbox_groups[raw_alias]
                changed = True
                continue
            if alias != raw_alias:
                del self.mailbox_groups[raw_alias]
                self.mailbox_groups[alias] = gid
                changed = True
            elif self.mailbox_groups[raw_alias] != gid:
                self.mailbox_groups[raw_alias] = gid
                changed = True

        if not isinstance(self.imap_configs, dict):
            self.imap_configs = {}
            changed = True

        for raw_id, config in list(self.imap_configs.items()):
            if not isinstance(config, dict):
                del self.imap_configs[raw_id]
                changed = True
                continue
            config_id = str(config.get("id") or raw_id).strip() or self._generate_imap_config_id()
            if config_id != raw_id:
                del self.imap_configs[raw_id]
                self.imap_configs[config_id] = config
                changed = True
            if config.get("id") != config_id:
                config["id"] = config_id
                changed = True
            for key in ("name", "email", "password", "host"):
                value = str(config.get(key) or "").strip()
                if config.get(key) != value:
                    config[key] = value
                    changed = True
            inferred_host = config.get("host") or infer_mail_host(config.get("email", ""))
            if config.get("host") != inferred_host:
                config["host"] = inferred_host
                changed = True
            try:
                normalized_port = coerce_mail_port(config.get("port"))
            except ValueError:
                normalized_port = 993
            if config.get("port") != normalized_port:
                config["port"] = normalized_port
                changed = True
            if not config.get("name"):
                config["name"] = config.get("email") or config_id
                changed = True
            if "created_at" not in config:
                config["created_at"] = datetime.now().isoformat()
                changed = True
            if "updated_at" not in config:
                config["updated_at"] = config.get("created_at") or datetime.now().isoformat()
                changed = True

        for account in self.accounts.values():
            if "group_id" in account:
                del account["group_id"]
                changed = True
            config_id = str(account.get("imap_config_id") or "").strip()
            if config_id and config_id not in self.imap_configs:
                del account["imap_config_id"]
                changed = True
        return changed

    def _group_id_from_input(self, group_id: Any, default: str = DEFAULT_GROUP_ID) -> str:
        gid = str(group_id or "").strip()
        if not gid:
            return default
        if gid not in self.groups:
            raise ValueError("分组不存在")
        return gid

    @staticmethod
    def _normalize_mailbox_email(alias_email: Any) -> str:
        return str(alias_email or "").strip().lower()

    def _group_for_id(self, group_id: str) -> Dict:
        gid = group_id if group_id in self.groups else DEFAULT_GROUP_ID
        return dict(self.groups.get(gid, self.groups[DEFAULT_GROUP_ID]))

    def get_mailbox_group(self, alias_email: str) -> Dict:
        self._normalize_loaded_state()
        alias = self._normalize_mailbox_email(alias_email)
        gid = self.mailbox_groups.get(alias, DEFAULT_GROUP_ID)
        return self._group_for_id(gid)

    def move_mailboxes_to_group(self, alias_emails: List[str], group_id: str) -> int:
        gid = self._group_id_from_input(group_id)
        moved = 0
        with self._lock:
            for alias_email in alias_emails:
                alias = self._normalize_mailbox_email(alias_email)
                if not alias or "@" not in alias:
                    continue
                if gid == DEFAULT_GROUP_ID:
                    if alias in self.mailbox_groups:
                        del self.mailbox_groups[alias]
                        moved += 1
                    continue
                if self.mailbox_groups.get(alias) != gid:
                    self.mailbox_groups[alias] = gid
                    moved += 1
            if moved:
                self._save()
        return moved

    def _movable_group_ids(self, exclude_group_id: Optional[str] = None) -> List[str]:
        return [
            group["id"] for group in sorted(
                self.groups.values(),
                key=lambda g: (int(g.get("sort_order") or 0), g.get("created_at", ""), g.get("id", "")),
            )
            if group.get("id") not in BUILTIN_GROUP_IDS and group.get("id") != exclude_group_id
        ]

    def _apply_group_order(self, group_ids: List[str]):
        for definition in BUILTIN_GROUP_DEFINITIONS:
            self.groups[definition["id"]]["sort_order"] = definition["sort_order"]
        for index, group_id in enumerate(group_ids, start=len(BUILTIN_GROUP_IDS)):
            self.groups[group_id]["sort_order"] = index

    def _set_group_position(self, group_id: str, sort_position: Optional[int]):
        if group_id in BUILTIN_GROUP_IDS:
            return
        group_ids = self._movable_group_ids(exclude_group_id=group_id)
        max_position = len(group_ids) + 1
        if sort_position is None:
            target = max_position
        else:
            target = max(1, min(int(sort_position), max_position))
        group_ids.insert(target - 1, group_id)
        self._apply_group_order(group_ids)

    def list_groups(self) -> List[Dict]:
        self._normalize_loaded_state()
        counts: Dict[str, int] = {gid: 0 for gid in self.groups}
        for gid in self.mailbox_groups.values():
            counts[gid] = counts.get(gid, 0) + 1
        groups = sorted(
            self.groups.values(),
            key=lambda g: (0 if g.get("id") in BUILTIN_GROUP_IDS else 1, int(g.get("sort_order") or 0), g.get("name", "")),
        )
        result = []
        for group in groups:
            item = dict(group)
            item["mailbox_count"] = counts.get(group["id"], 0)
            result.append(item)
        return result

    def get_group(self, group_id: str) -> Optional[Dict]:
        group = self.groups.get(str(group_id or "").strip())
        if not group:
            return None
        item = dict(group)
        item["mailbox_count"] = sum(1 for gid in self.mailbox_groups.values() if gid == item["id"])
        return item

    def add_group(self, name: str, description: str = "", color: str = "", sort_position: Optional[int] = None) -> Dict:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("分组名称不能为空")
        lowered = clean_name.lower()
        if any(str(g.get("name") or "").strip().lower() == lowered for g in self.groups.values()):
            raise ValueError("分组名称已存在")
        with self._lock:
            group_id = self._generate_group_id()
            self.groups[group_id] = {
                "id": group_id,
                "name": clean_name,
                "description": str(description or "").strip(),
                "color": normalize_group_color(color),
                "sort_order": 999999,
                "is_default": False,
                "created_at": datetime.now().isoformat(),
            }
            self._set_group_position(group_id, sort_position)
            self._save()
            return self.get_group(group_id) or dict(self.groups[group_id])

    def update_group(self, group_id: str, name: str, description: str = "", color: str = "", sort_position: Optional[int] = None) -> Dict:
        gid = str(group_id or "").strip()
        if gid in BUILTIN_GROUP_IDS:
            raise ValueError("内置状态分组不能编辑")
        if gid not in self.groups:
            raise KeyError("分组不存在")
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("分组名称不能为空")
        lowered = clean_name.lower()
        for other_id, group in self.groups.items():
            if other_id != gid and str(group.get("name") or "").strip().lower() == lowered:
                raise ValueError("分组名称已存在")
        with self._lock:
            self.groups[gid].update({
                "name": clean_name,
                "description": str(description or "").strip(),
                "color": normalize_group_color(color),
            })
            self._set_group_position(gid, sort_position)
            self._save()
            return self.get_group(gid) or dict(self.groups[gid])

    def delete_group(self, group_id: str) -> bool:
        gid = str(group_id or "").strip()
        if gid in BUILTIN_GROUP_IDS:
            raise ValueError("内置状态分组不能删除")
        if gid not in self.groups:
            raise KeyError("分组不存在")
        with self._lock:
            self.mailbox_groups = {
                alias: assigned_gid
                for alias, assigned_gid in self.mailbox_groups.items()
                if assigned_gid != gid
            }
            del self.groups[gid]
            self._apply_group_order(self._movable_group_ids())
            self._save()
        return True

    def reorder_groups(self, group_ids: List[str]) -> bool:
        normalized = [str(gid or "").strip() for gid in group_ids]
        movable = self._movable_group_ids()
        if set(normalized) != set(movable) or len(normalized) != len(movable):
            raise ValueError("分组排序参数无效")
        with self._lock:
            self._apply_group_order(normalized)
            self._save()
        return True


    @staticmethod
    def _account_identity_values(account: Dict) -> set[str]:
        values = set()
        for key in ("real_email", "icloud_email", "session_fingerprint"):
            value = str(account.get(key) or "").strip().lower()
            if value:
                values.add(value)
        return values

    def _find_account_by_identity(self, account: Dict) -> Optional[Dict]:
        identities = self._account_identity_values(account)
        if not identities:
            return None
        current_id = str(account.get("id") or "")
        for existing in self.accounts.values():
            if current_id and existing.get("id") == current_id:
                continue
            if identities & self._account_identity_values(existing):
                return existing
        return None

    def _find_blocking_duplicate(self, account: Dict) -> Optional[Dict]:
        """判重时只让"仍可用"的同身份账号构成冲突。

        两份 cookie 指向同一 Apple 身份时，旧逻辑会把双方都降级为 error 并互相
        引用，谁都无法恢复。只在对方仍是 active 时才降级，可留出自愈路径。
        """
        duplicate = self._find_account_by_identity(account)
        if duplicate and duplicate.get("status") == "active":
            return duplicate
        return None

    @staticmethod
    def parse_cookie_input(raw: str) -> Dict[str, str]:
        raw = raw.strip()
        if not raw:
            raise ValueError("空白输入 — 请粘贴 Cookie Header String 或 JSON")

        if raw.startswith("{"):
            try:
                cookies = json.loads(raw)
                if isinstance(cookies, dict):
                    return {k: str(v) for k, v in cookies.items() if v}
            except json.JSONDecodeError:
                pass

        cookies: Dict[str, str] = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                name = name.strip()
                value = value.strip()
                if name:
                    cookies[name] = value

        if not cookies:
            raise ValueError(
                "无法解析 Cookie 输入。\n"
                "请提供 Header String 格式 (name=value; ...) 或 JSON 格式"
            )

        return cookies

    @staticmethod
    def cookies_to_header(cookies: Dict) -> str:
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    @staticmethod
    def _session_fingerprint(cookies: Dict, host: str) -> str:
        payload = json.dumps(
            {"host": str(host or "icloud.com"), "cookies": cookies or {}},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _mark_session_pending(account: Dict):
        account["status"] = "pending"
        account["validation_status"] = "queued"
        account["validation_started_at"] = None
        account["last_error"] = None

    @staticmethod
    def _next_session_version(account: Optional[Dict] = None) -> int:
        if not account:
            return 1
        try:
            return int(account.get("session_version") or 0) + 1
        except (TypeError, ValueError):
            return 1

    def _refresh_session_state(self, account: Dict) -> Dict:
        from icloud_hme import ICloudHME

        previous_status = str(account.get("status") or "")
        try:
            client = ICloudHME(
                account["cookies"],
                host=account.get("host", "icloud.com"),
                verbose=False,
            )
            client.validate_session()
            info = client.get_account_info()
            if info:
                account["real_email"] = (
                    info.get("appleId", "")
                    or info.get("primaryEmail", "")
                )
                account["icloud_email"] = self._derive_icloud_email(info)

            try:
                aliases = client.list_aliases()
                account["alias_total"] = len(aliases)
                account["alias_active"] = sum(
                    1 for a in aliases if a.get("active")
                )
            except Exception:
                pass

            account["status"] = "active"
            account["validation_status"] = "ok"
            account["last_validated"] = datetime.now().isoformat()
            account["last_error"] = None
        except Exception as e:
            err = str(e)[:300]
            account["last_error"] = err
            account["last_validated"] = datetime.now().isoformat()
            hard = is_hard_session_error(err)
            # Active accounts stay active on soft/transient failures (reboot proxy blip).
            if previous_status == "active" and not hard:
                account["status"] = "active"
                account["validation_status"] = "degraded"
            else:
                account["status"] = "error"
                account["validation_status"] = "error"

        return account

    def add_account(
        self, name: str, cookie_input: str, host: str = "icloud.com",
        group_id: str = "", validate: bool = True,
    ) -> Dict:
        cookies = self.parse_cookie_input(cookie_input)
        acc_id = self._generate_id()
        clean_host = normalize_account_host(host)

        account: Dict[str, Any] = {
            "id": acc_id,
            "name": str(name or "未命名账号").strip() or "未命名账号",
            "real_email": "",
            "icloud_email": "",
            "cookies": cookies,
            "host": clean_host,
            "status": "active",
            "validation_status": "ok" if validate else "queued",
            "validation_started_at": None,
            "alias_total": 0,
            "alias_active": 0,
            "last_validated": None,
            "last_error": None,
            "session_version": 1,
            "session_fingerprint": self._session_fingerprint(cookies, clean_host),
            "created_at": datetime.now().isoformat(),
        }

        if validate:
            self._refresh_session_state(account)
        else:
            self._mark_session_pending(account)
        with self._lock:
            duplicate = self._find_account_by_identity(account)
            if duplicate:
                account["id"] = duplicate["id"]
                account["name"] = duplicate.get("name") or account["name"]
                account["created_at"] = duplicate.get("created_at") or account["created_at"]
                account["session_version"] = self._next_session_version(duplicate)
                for key in ("mail_email", "mail_password", "mail_host", "mail_port", "imap_config_id"):
                    if duplicate.get(key) and not account.get(key):
                        account[key] = duplicate[key]
                self.accounts[duplicate["id"]] = account
            else:
                self.accounts[acc_id] = account
            self._save()
        return dict(account)

    def get_account_session(self, acc_id: str) -> Dict:
        account = self.accounts.get(acc_id)
        if not account:
            raise KeyError(f"账号不存在: {acc_id}")
        return {
            "id": account.get("id", acc_id),
            "name": account.get("name", ""),
            "real_email": account.get("real_email", ""),
            "host": account.get("host", "icloud.com"),
            "status": account.get("status", ""),
            "cookie_input": self.cookies_to_header(account.get("cookies", {})),
        }

    def update_account_session(
        self, acc_id: str, name: str, cookie_input: str, host: str = "icloud.com",
        group_id: Optional[str] = None, validate: bool = True,
    ) -> Dict:
        cookies = self.parse_cookie_input(cookie_input)
        with self._lock:
            current = self.accounts.get(acc_id)
            if not current:
                raise KeyError(f"账号不存在: {acc_id}")
            account = dict(current)

        clean_host = normalize_account_host(host or account.get("host") or "icloud.com")
        account.update({
            "name": str(name or account.get("name") or "未命名账号").strip() or "未命名账号",
            "cookies": cookies,
            "host": clean_host,
            "session_version": self._next_session_version(account),
            "session_fingerprint": self._session_fingerprint(cookies, clean_host),
        })
        account.pop("group_id", None)
        if validate:
            self._refresh_session_state(account)
        else:
            self._mark_session_pending(account)

        with self._lock:
            if acc_id not in self.accounts:
                raise KeyError(f"账号不存在: {acc_id}")
            duplicate = self._find_blocking_duplicate(account)
            if duplicate and duplicate.get("id") != acc_id:
                label = duplicate.get("name") or duplicate.get("real_email") or duplicate.get("id")
                raise ValueError(f"该会话属于已存在账号: {label}")
            self.accounts[acc_id] = account
            self._save()
            return dict(account)

    def remove_account(self, acc_id: str) -> bool:
        if acc_id in self.accounts:
            del self.accounts[acc_id]
            self._save()
            return True
        return False

    def get_account(self, acc_id: str) -> Optional[Dict]:
        account = self.accounts.get(acc_id)
        if not account:
            return None
        return dict(account)

    def list_accounts(self) -> List[Dict]:
        return sorted(
            (dict(account) for account in self.accounts.values()),
            key=lambda a: (a.get("status") != "active", a.get("created_at", "")),
        )

    @staticmethod
    def _public_imap_config(config: Dict) -> Dict:
        return {
            "id": config.get("id", ""),
            "name": config.get("name", ""),
            "email": config.get("email", ""),
            "host": config.get("host", ""),
            "port": int(config.get("port") or 993),
            "has_password": bool(config.get("password")),
            "created_at": config.get("created_at", ""),
            "updated_at": config.get("updated_at", ""),
        }

    @staticmethod
    def _imap_config_payload(
        name: str, email: str, password: str, host: str = "",
        port: int = 993, existing_password: str = "",
    ) -> Dict[str, Any]:
        email = str(email or "").strip()
        password = str(password or "").strip() or str(existing_password or "").strip()
        host = str(host or "").strip() or infer_mail_host(email)
        port = coerce_mail_port(port)
        if not email:
            raise ValueError("请填写 IMAP 登录邮箱")
        if not password:
            raise ValueError("请填写邮箱授权码或密码")
        if not host:
            raise ValueError("请填写 IMAP 服务器")
        return {
            "name": str(name or email).strip() or email,
            "email": email,
            "password": password,
            "host": host,
            "port": port,
        }

    def list_imap_configs(self) -> List[Dict]:
        return [
            self._public_imap_config(config)
            for config in sorted(
                self.imap_configs.values(),
                key=lambda item: (str(item.get("name") or ""), str(item.get("created_at") or "")),
            )
        ]

    def get_imap_config(self, config_id: str) -> Dict:
        config = self.imap_configs.get(str(config_id or "").strip())
        if not config:
            raise KeyError(f"IMAP 配置不存在: {config_id}")
        return self._public_imap_config(config)

    def add_imap_config(self, name: str, email: str, password: str,
                        host: str = "", port: int = 993) -> Dict:
        now = datetime.now().isoformat()
        config_id = self._generate_imap_config_id()
        config = self._imap_config_payload(name, email, password, host, port)
        config.update({"id": config_id, "created_at": now, "updated_at": now})
        with self._lock:
            self.imap_configs[config_id] = config
            self._save()
            return self._public_imap_config(config)

    def update_imap_config(self, config_id: str, name: str, email: str,
                           password: str = "", host: str = "", port: int = 993) -> Dict:
        config_id = str(config_id or "").strip()
        with self._lock:
            current = self.imap_configs.get(config_id)
            if not current:
                raise KeyError(f"IMAP 配置不存在: {config_id}")
            updated = self._imap_config_payload(
                name,
                email,
                password,
                host,
                port,
                existing_password=current.get("password", ""),
            )
            updated.update({
                "id": config_id,
                "created_at": current.get("created_at") or datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            })
            self.imap_configs[config_id] = updated
            self._save()
            return self._public_imap_config(updated)

    def delete_imap_config(self, config_id: str) -> bool:
        config_id = str(config_id or "").strip()
        if not config_id:
            return False
        with self._lock:
            if config_id not in self.imap_configs:
                return False
            for account in self.accounts.values():
                if account.get("imap_config_id") == config_id:
                    raise ValueError("IMAP 配置正在被账号使用，先切换账号邮件登录")
            del self.imap_configs[config_id]
            self._save()
            return True

    def get_account_mail_settings(self, account_or_id: Any, include_password: bool = False) -> Dict:
        account = self.accounts.get(account_or_id) if isinstance(account_or_id, str) else account_or_id
        if not account:
            raise KeyError(f"账号不存在: {account_or_id}")
        config_id = str(account.get("imap_config_id") or "").strip()
        config = self.imap_configs.get(config_id) if config_id else None
        if config:
            settings = {
                "mail_email": config.get("email", ""),
                "mail_host": config.get("host", ""),
                "mail_port": int(config.get("port") or 993),
                "imap_config_id": config_id,
                "imap_config_name": config.get("name", ""),
                "has_mail_config": bool(config.get("email") and config.get("host") and config.get("password")),
            }
            if include_password:
                settings["mail_password"] = config.get("password", "")
            return settings
        settings = {
            "mail_email": account_mail_email(account),
            "mail_host": account_mail_host(account),
            "mail_port": int(account.get("mail_port") or 993),
            "imap_config_id": "",
            "imap_config_name": "",
            "has_mail_config": account_has_mail_config(account),
        }
        if include_password:
            settings["mail_password"] = account.get("mail_password") or account.get("app_password", "")
        return settings

    def update_account(self, acc_id: str, **kwargs) -> Optional[Dict]:
        with self._lock:
            if acc_id in self.accounts:
                kwargs.pop("group_id", None)
                self.accounts[acc_id].update(kwargs)
                self._save()
                return dict(self.accounts[acc_id])
            return None

    @staticmethod
    def _derive_icloud_email(info: Dict) -> str:
        primary = str(info.get("primaryEmail", "") or "").strip()
        apple_id = str(info.get("appleId", "") or "").strip()

        if primary and ("@icloud.com" in primary or "@me.com" in primary or "@mac.com" in primary):
            return primary

        if apple_id and ("@icloud.com" in apple_id or "@me.com" in apple_id or "@mac.com" in apple_id):
            return apple_id

        if apple_id and "@" in apple_id:
            local = apple_id.split("@")[0]
            return f"{local}@icloud.com"

        return primary or apple_id

    def validate_account(
        self,
        acc_id: str,
        expected_session_version: Optional[int] = None,
        *,
        reason: str = "manual",
    ) -> Dict:
        with self._lock:
            account = self.accounts.get(acc_id)
            if not account:
                raise KeyError(f"账号不存在: {acc_id}")
            if expected_session_version is not None:
                current_version = int(account.get("session_version") or 0)
                if current_version != int(expected_session_version):
                    return dict(account)
            working = dict(account)

        reason_key = str(reason or "manual").strip().lower() or "manual"
        # Health checks must not re-apply identity collision after account exists.
        check_duplicate = reason_key not in {"health"}

        working["validation_status"] = "running"
        working["validation_started_at"] = datetime.now().isoformat()
        self._refresh_session_state(working)

        with self._lock:
            current = self.accounts.get(acc_id)
            if not current:
                raise KeyError(f"账号不存在: {acc_id}")
            if expected_session_version is not None:
                current_version = int(current.get("session_version") or 0)
                if current_version != int(expected_session_version):
                    return dict(current)
            if check_duplicate:
                duplicate = self._find_blocking_duplicate(working)
                if duplicate and duplicate.get("id") != acc_id:
                    working["status"] = "error"
                    working["validation_status"] = "error"
                    label = (
                        duplicate.get("name")
                        or duplicate.get("real_email")
                        or duplicate.get("id")
                    )
                    working["last_error"] = f"该会话属于已存在账号: {label}"
            self.accounts[acc_id] = working
            self._save()
            return dict(working)

    def validate_all(self) -> List[Dict]:
        results: List[Dict] = []
        for acc_id in list(self.accounts.keys()):
            try:
                account = self.validate_account(acc_id)
                results.append({
                    "id": acc_id,
                    "ok": account.get("status") == "active",
                    "email": account.get("real_email", ""),
                    "alias_total": account.get("alias_total", 0),
                })
            except Exception as e:
                results.append({
                    "id": acc_id,
                    "ok": False,
                    "error": str(e)[:200],
                })
        return results

    def get_client(self, acc_id: str, verbose: bool = False):
        from icloud_hme import ICloudHME

        account = self.accounts.get(acc_id)
        if not account:
            raise KeyError(f"账号不存在: {acc_id}")
        return ICloudHME(
            account["cookies"],
            host=account.get("host", "icloud.com"),
            verbose=verbose,
        )


    def get_mail_client(self, acc_id: str, verbose: bool = False):
        from icloud_mail import ICloudMail

        account = self.accounts.get(acc_id)
        if not account:
            raise KeyError(f"账号不存在: {acc_id}")
        settings = self.get_account_mail_settings(account, include_password=True)
        mail_email = settings.get("mail_email", "")
        mail_host = settings.get("mail_host", "")
        mail_port = settings.get("mail_port") or 993
        mail_password = settings.get("mail_password", "")
        if not mail_email or not mail_host or not mail_password:
            raise ValueError("邮件读取未配置，请在账号卡片中设置邮件登录")
        return ICloudMail(mail_email, mail_password, verbose=verbose, server=mail_host, port=mail_port)

    def set_mail_settings(self, acc_id: str, email: str, password: str,
                          host: str = "", port: int = 993,
                          imap_config_id: str = "") -> Dict:
        imap_config_id = str(imap_config_id or "").strip()
        with self._lock:
            if acc_id not in self.accounts:
                raise KeyError(f"账号不存在: {acc_id}")
            account = self.accounts[acc_id]
            if imap_config_id:
                if imap_config_id not in self.imap_configs:
                    raise KeyError(f"IMAP 配置不存在: {imap_config_id}")
                account["imap_config_id"] = imap_config_id
                self._save()
                return dict(account)

            payload = self._imap_config_payload(email, email, password, host, port)
            account.update({
                "mail_email": payload["email"],
                "mail_password": payload["password"],
                "mail_host": payload["host"],
                "mail_port": payload["port"],
            })
            account.pop("imap_config_id", None)
            self._save()
            return dict(account)

    def test_imap_config(self, config_id: str) -> Dict:
        from icloud_mail import ICloudMail

        config = self.imap_configs.get(str(config_id or "").strip())
        if not config:
            raise KeyError(f"IMAP 配置不存在: {config_id}")
        mail = ICloudMail(
            config.get("email", ""),
            config.get("password", ""),
            server=config.get("host", ""),
            port=config.get("port") or 993,
        )
        result = mail.test_connection()
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "邮件读取暂不可用"}
        return result

    def test_mail_connection(self, acc_id: str) -> Dict:
        mail = self.get_mail_client(acc_id)
        result = mail.test_connection()
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "邮件读取暂不可用"}
        return result

    def test_mail_read(self, acc_id: str, alias_email: str = "",
                       limit: int = 5, days: int = 7) -> Dict:
        alias_email = str(alias_email or "").strip()
        limit = max(1, min(int(limit or 5), 20))
        days = max(1, min(int(days or 7), 90))
        try:
            result = self.test_mail_connection(acc_id)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200] or "邮件读取暂不可用"}
        if not result.get("ok"):
            return result

        mail = self.get_mail_client(acc_id)
        try:
            if alias_email:
                messages = mail.find_by_recipient(alias_email, limit=limit, days=days)
            else:
                messages = mail.check_inbox(limit=limit, days=days)
        except Exception as e:
            result["ok"] = False
            result["error"] = str(e)[:200] or "邮件读取暂不可用"
            result["alias"] = alias_email
            result["days"] = days
            result["recent_count"] = 0
            result["messages"] = []
            return result
        finally:
            try:
                mail.disconnect()
            except Exception:
                pass

        result.update({
            "ok": True,
            "alias": alias_email,
            "days": days,
            "limit": limit,
            "recent_count": len(messages),
            "messages": messages[:limit],
        })
        if messages:
            result["message"] = f"IMAP 正常，读取到 {len(messages)} 封邮件"
        else:
            scope = f"发给 {alias_email} 的" if alias_email else ""
            result["message"] = f"IMAP 正常，但最近 {days} 天没有读取到{scope}邮件"
        return result

    def check_inbox(self, acc_id: str, limit: int = 50, days: int = 7,
                    force: bool = False) -> List[Dict]:
        limit = max(1, min(int(limit or 50), 100))
        days = max(1, min(int(days or 7), 90))
        cached = self._cache.get_inbox(acc_id)
        age = self._cache.cache_age_seconds(acc_id)

        if not force and cached and age < 300:
            return cached[-limit:]

        mail = None
        try:
            mail = self.get_mail_client(acc_id)
            new_msgs = mail.check_inbox(limit=limit, days=days)
        except Exception as e:
            cached = self._cache.get_inbox(acc_id)
            if cached:
                return cached[-limit:]
            raise RuntimeError(str(e) or "邮件读取暂不可用") from e
        finally:
            if mail:
                try:
                    mail.disconnect()
                except Exception:
                    pass

        if new_msgs:
            self._cache.set_inbox(acc_id, new_msgs)

        return self._cache.get_inbox(acc_id)[-limit:]

    def check_alias_mail(self, acc_id: str, alias_email: str,
                         limit: int = 20, days: int = 30,
                         force: bool = False) -> List[Dict]:
        limit = max(1, min(int(limit or 20), 100))
        days = max(1, min(int(days or 30), 90))
        cached = self._cache.get_alias_mail(acc_id, alias_email)
        age = self._cache.cache_age_seconds(acc_id)

        if not force and cached and age < 300:
            return cached[-limit:]

        mail = None
        try:
            mail = self.get_mail_client(acc_id)
            new_msgs = mail.find_by_recipient(alias_email, limit=limit, days=days)
        except Exception as e:
            cached = self._cache.get_alias_mail(acc_id, alias_email)
            if cached:
                return cached[-limit:]
            raise RuntimeError(str(e) or "邮件读取暂不可用") from e
        finally:
            if mail:
                try:
                    mail.disconnect()
                except Exception:
                    pass

        if new_msgs:
            self._cache.set_alias_mail(acc_id, alias_email, new_msgs)

        return self._cache.get_alias_mail(acc_id, alias_email)[-limit:]

    def check_all_aliases_mail(self, acc_id: str, limit_per: int = 5,
                               days: int = 14,
                               force: bool = False) -> Dict[str, List[Dict]]:
        cached = self._cache.get_all_alias_mail(acc_id)
        age = self._cache.cache_age_seconds(acc_id)

        if not force and cached and age < 300:
            results = {}
            for alias, msgs in cached.items():
                results[alias] = msgs[-limit_per:]
            return results

        from icloud_hme import ICloudHME

        try:
            client = self.get_client(acc_id, verbose=False)
            aliases = client.list_aliases()
        except Exception:
            return cached if cached else {}

        alias_set = {a.get("email", "").lower() for a in aliases if a.get("email")}
        if not alias_set:
            return cached if cached else {}

        try:
            mail = self.get_mail_client(acc_id)
            all_inbox = mail.check_inbox(limit=100, days=days)
            mail.disconnect()
        except Exception:
            return cached if cached else {}

        results: Dict[str, List[Dict]] = {}
        for msg in all_inbox:
            to_field = msg.get("to", "").lower()
            for alias in alias_set:
                if alias and alias in to_field:
                    if alias not in results:
                        results[alias] = []
                    if len(results[alias]) < limit_per:
                        results[alias].append(msg)
                    break

        if results:
            self._cache.set_alias_mail_batch(acc_id, results)

        return results


    def get_verification_codes(self, acc_id: str, alias_email: str = "",
                               limit: int = 10, days: int = 1) -> List[Dict]:
        mail = self.get_mail_client(acc_id)
        try:
            return mail.find_verification_codes(alias_email, limit=limit, days=days)
        finally:
            mail.disconnect()

    def generate_alias_candidate(self, acc_id: str) -> Dict:
        client = self.get_client(acc_id, verbose=False)
        return client.generate_alias()

    def reserve_alias_for_account(self, acc_id: str, hme: str,
                                  label: str = "", note: str = "") -> Dict:
        client = self.get_client(acc_id, verbose=False)
        alias = client.reserve_alias(hme, label or None, note)
        self._refresh_alias_counts(acc_id, client)
        return alias

    def deactivate_alias_for_account(self, acc_id: str, anonymous_id: str) -> bool:
        client = self.get_client(acc_id, verbose=False)
        ok = client.deactivate(anonymous_id)
        self._refresh_alias_counts(acc_id, client)
        return ok

    def delete_alias_for_account(self, acc_id: str, anonymous_id: str) -> bool:
        client = self.get_client(acc_id, verbose=False)
        ok = client.delete(anonymous_id)
        self._refresh_alias_counts(acc_id, client)
        return ok

    def _refresh_alias_counts(self, acc_id: str, client=None):
        account = self.accounts.get(acc_id)
        if not account:
            raise KeyError(f"账号不存在: {acc_id}")
        if client is None:
            client = self.get_client(acc_id, verbose=False)
        aliases = client.list_aliases()
        account["alias_total"] = len(aliases)
        account["alias_active"] = sum(1 for a in aliases if a.get("active"))
        account["last_validated"] = datetime.now().isoformat()
        account["last_error"] = None
        self._save()

    def create_aliases_for_account(
        self, acc_id: str, count: int = 1, label: str = "",
        note: str = ""
    ) -> List[Dict]:
        from icloud_hme import ICloudHME

        account = self.accounts.get(acc_id)
        if not account:
            raise KeyError(f"账号不存在: {acc_id}")

        client = ICloudHME(
            account["cookies"],
            host=account.get("host", "icloud.com"),
            verbose=False,
        )

        results: List[Dict] = []
        for i in range(count):
            # 连续无间隔创建必被 Apple 限流；调度器路径本就有间隔，这里补齐。
            if i > 0 and CREATE_INTERVAL_SEC > 0:
                time.sleep(CREATE_INTERVAL_SEC)
            try:
                alias_label = label or (
                    f"{account.get('name', acc_id)} "
                    f"{datetime.now().strftime('%m%d%H%M')}-{i + 1}"
                )
                result = client.create_alias(
                    label=alias_label,
                    note=note or None,
                    max_retries=3,
                )
                email = result.get("hme") or result.get("email", "")
                if email:
                    item = dict(result)
                    item.update({"email": email, "account_id": acc_id, "ok": True})
                    results.append(item)
                    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                    # 用当前 RESULTS_DIR 拼路径，测试重定向结果目录时不会写进真实数据。
                    with open(str(RESULTS_DIR / "latest_emails.txt"), "a", encoding="utf-8") as f:
                        # 第三列为创建时间毫秒；缺了它列表就无法按时间排序。
                        f.write(f"{email}\t{acc_id}\t{int(time.time() * 1000)}\n")
                    account["alias_total"] = account.get("alias_total", 0) + 1
                    account["alias_active"] = account.get("alias_active", 0) + 1
                else:
                    results.append({
                        "email": None,
                        "account_id": acc_id,
                        "ok": False,
                        "error": "create_alias 返回空邮箱",
                    })
            except Exception as e:
                err_str = str(e)
                results.append({
                    "email": None,
                    "account_id": acc_id,
                    "ok": False,
                    "error": err_str[:200],
                })
                lower = err_str.lower()
                if any(kw in lower for kw in (
                    "limit", "exceeded", "maximum", "quota", "429",
                    "too many", "rate", "throttle",
                )):
                    break

        self._save()
        return results

    def create_aliases_batch(
        self,
        account_ids: List[str],
        count_per_account: int = 1,
        interval_sec: float = 3.0,
        label: str = "",
    ) -> Dict[str, List[Dict]]:
        all_results: Dict[str, List[Dict]] = {}
        for i, acc_id in enumerate(account_ids):
            if acc_id not in self.accounts:
                all_results[acc_id] = [{
                    "email": None, "account_id": acc_id,
                    "ok": False, "error": "账号不存在",
                }]
                continue
            if self.accounts[acc_id].get("status") != "active":
                all_results[acc_id] = [{
                    "email": None, "account_id": acc_id,
                    "ok": False, "error": "账号不可用",
                }]
                continue

            results = self.create_aliases_for_account(
                acc_id, count_per_account, label
            )
            all_results[acc_id] = results

            if i < len(account_ids) - 1 and interval_sec > 0:
                time.sleep(interval_sec)

        return all_results

    def get_aliases_for_account(self, acc_id: str) -> List[Dict]:
        try:
            client = self.get_client(acc_id, verbose=False)
            return client.list_aliases()
        except Exception:
            return []

    def get_all_aliases(self) -> List[Dict]:
        all_aliases: List[Dict] = []
        for acc_id, account in self.accounts.items():
            for alias in self.get_aliases_for_account(acc_id):
                alias_email = alias.get("email") or alias.get("hme") or ""
                group = self.get_mailbox_group(alias_email)
                alias["account_id"] = acc_id
                alias["account_name"] = account.get("name", "")
                alias["account_email"] = account.get("real_email", "")
                alias["group_id"] = group.get("id", DEFAULT_GROUP_ID)
                alias["group_name"] = group.get("name", "")
                alias["group_color"] = group.get("color", "")
                all_aliases.append(alias)
        return all_aliases

    def get_summary(self) -> Dict:
        total_aliases = sum(
            a.get("alias_total", 0) for a in self.accounts.values()
        )
        total_active = sum(
            a.get("alias_active", 0) for a in self.accounts.values()
        )
        active_accounts = sum(
            1 for a in self.accounts.values() if a.get("status") == "active"
        )
        error_accounts = sum(
            1 for a in self.accounts.values() if a.get("status") == "error"
        )
        return {
            "account_count": len(self.accounts),
            "active_accounts": active_accounts,
            "error_accounts": error_accounts,
            "total_aliases": total_aliases,
            "total_active_aliases": total_active,
        }


if __name__ == "__main__":
    print("AccountManager 自测")
    mgr = AccountManager()
    summary = mgr.get_summary()
    print(f"当前账号数: {summary['account_count']}")

    header = "X_APPLE_WEB_KB=abc123; SESSION_TOKEN=xyz789"
    parsed = mgr.parse_cookie_input(header)
    print(f"Header String → {len(parsed)} 个 cookie")

    json_in = '{"X_APPLE_WEB_KB":"abc123","SESSION_TOKEN":"xyz789"}'
    parsed2 = mgr.parse_cookie_input(json_in)
    print(f"JSON → {len(parsed2)} 个 cookie")

    print("自测完成 ✓")
