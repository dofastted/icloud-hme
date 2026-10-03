#!/usr/bin/env python3
"""Mailbox query service.

JSON contracts:

MailboxSummary:
    alias_email, account_id, account_name, label, is_active, created_at, shared

MessageSummary:
    message_id, subject, from, to, date, body_preview

SharedPublicView:
    mailbox, label, message, fetched_at, cache_age_sec

Public serialization is whitelist-based. Internal account fields, iCloud
addresses, cookies, app passwords, anonymous ids and stack traces must not be
added to ``SharedPublicView``.
"""

import json
import time
from email.utils import getaddresses, parsedate_to_datetime
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from account_manager import AccountManager, LATEST_EMAILS, DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, DEFAULT_GROUP_COLOR
from shared_mailboxes import SharedMailboxStore

MAILBOX_INDEX_FILE = LATEST_EMAILS.parent / "mailbox_index.json"
MAIL_CACHE_TTL_SECONDS = 300
MAIL_READ_UNAVAILABLE_MESSAGE = "邮件读取暂不可用"
REMOTE_DELETE_PAUSE_SECONDS = 0.35
ALIAS_ID_UNRESOLVED_MESSAGE = (
    "无法从 Apple 获取该别名标识，请先云端同步或确认账号会话有效；"
    "如只想清理本地记录，请使用 local_only=1"
)


def _mail_read_error(exc: Exception) -> Exception:
    message = str(exc).lower()
    if isinstance(exc, ValueError):
        return IMAPNotConfigured(MAIL_READ_UNAVAILABLE_MESSAGE)
    if "邮件登录失败" in str(exc) or "邮件登录认证失败" in str(exc) or "authentication" in message or "login" in message:
        return IMAPNotConfigured(MAIL_READ_UNAVAILABLE_MESSAGE)
    return IMAPUnavailable(MAIL_READ_UNAVAILABLE_MESSAGE)


def _raise_mail_read_error(exc: Exception):
    raise _mail_read_error(exc) from exc


class MailboxNotFound(KeyError):
    """Mailbox alias was not found in local or persisted alias indexes."""


class IMAPNotConfigured(ValueError):
    """The owning account cannot read mailbox messages yet."""


class IMAPUnavailable(RuntimeError):
    """The mail backend failed while reading messages."""


class AliasRemoteIdUnavailable(RuntimeError):
    """Apple alias id could not be resolved, so remote deletion is unsafe."""


def normalize_mail_filter(value: str) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"claude", "openai", "empty"} else ""


def _message_provider_flags(message: Optional[Dict]) -> Dict[str, bool]:
    if not message:
        return {"claude": False, "openai": False}
    text = " ".join(
        str(message.get(key) or "")
        for key in ("subject", "from", "to", "body_preview", "body", "text")
    ).lower()
    return {"claude": "claude" in text, "openai": "openai" in text}


class MailboxService:
    """Mailbox lookup, cached mail access, and mailbox deletion service."""

    def __init__(
        self,
        account_mgr: AccountManager,
        shared_store: Optional[SharedMailboxStore] = None,
        latest_emails_path: Path = LATEST_EMAILS,
        index_path: Path = MAILBOX_INDEX_FILE,
    ):
        self.account_mgr = account_mgr
        self.shared_store = shared_store
        self.latest_emails_path = Path(latest_emails_path)
        self.index_path = Path(index_path)

    def list_mailboxes(
        self,
        q: str = "",
        account_id: str = "",
        group_id: str = "",
        status: str = "",
        refresh: bool = False,
        sort: str = "",
        mail_kind: str = "",
    ) -> List[Dict]:
        accounts = {a.get("id"): a for a in self.account_mgr.list_accounts()}
        by_alias: Dict[str, Dict] = {}

        for cached in self._load_index():
            self._merge_source(by_alias, accounts, cached)

        for local in self._load_local_aliases():
            self._merge_source(by_alias, accounts, local)

        if refresh:
            remote_aliases = self._fetch_remote_aliases()
            self._save_index(remote_aliases)
            for remote in remote_aliases:
                self._merge_source(by_alias, accounts, remote)

        items = list(by_alias.values())
        for item in items:
            item["shared"] = (
                self.shared_store.get_for_alias(item["alias_email"])
                if self.shared_store
                else None
            )
            cached = self._cached_alias_messages(
                item.get("account_id", ""), item["alias_email"], allow_stale=True
            )
            item["is_empty"] = not cached
            flags = {"claude": False, "openai": False}
            for message in cached:
                message_flags = _message_provider_flags(message)
                flags["claude"] |= message_flags["claude"]
                flags["openai"] |= message_flags["openai"]
            item["has_claude"] = flags["claude"]
            item["has_openai"] = flags["openai"]
            item["latest_subject"] = str(cached[0].get("subject") or "") if cached else ""

        needle = q.strip().lower()
        if needle:
            items = [
                item for item in items
                if needle in item["alias_email"].lower()
                or needle in str(item.get("label") or "").lower()
                or needle in str(item.get("account_name") or "").lower()
                or needle in str(item.get("latest_subject") or "").lower()
            ]
        if account_id:
            items = [item for item in items if item.get("account_id") == account_id]
        if group_id:
            items = [item for item in items if item.get("group_id") == group_id]

        if status:
            normalized = status.lower()
            if normalized in ("active", "enabled"):
                items = [item for item in items if item.get("is_active") is True]
            elif normalized in ("inactive", "disabled", "revoked"):
                items = [item for item in items if item.get("is_active") is False]
        normalized_kind = normalize_mail_filter(mail_kind)
        if normalized_kind:
            if normalized_kind == "empty":
                items = [item for item in items if item["is_empty"]]
            else:
                items = [item for item in items if item[f"has_{normalized_kind}"]]

        sort_mode = normalize_mailbox_sort(sort)
        return sorted(items, key=lambda item: _mailbox_sort_key(item, sort_mode))
    def account_mail_attributes(self) -> Dict[str, Dict[str, bool]]:
        attributes = {
            str(account.get("id") or ""): {"has_claude": False, "has_openai": False}
            for account in self.account_mgr.list_accounts()
            if account.get("id")
        }
        for mailbox in self.list_mailboxes():
            account = attributes.get(str(mailbox.get("account_id") or ""))
            if not account:
                continue
            account["has_claude"] |= bool(mailbox.get("has_claude"))
            account["has_openai"] |= bool(mailbox.get("has_openai"))
        return attributes



    def refresh_mailboxes(self) -> List[Dict]:
        return self.list_mailboxes(refresh=True)

    def local_metadata(self) -> Dict:
        index_updated_at = None
        index_count = 0
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                index_updated_at = data.get("updated_at") if isinstance(data, dict) else None
                mailboxes = data.get("mailboxes", {}) if isinstance(data, dict) else {}
                index_count = len(mailboxes) if isinstance(mailboxes, (dict, list)) else 0
            except (json.JSONDecodeError, OSError):
                index_updated_at = None
        return {
            "source": "local",
            "index_updated_at": index_updated_at,
            "local_alias_count": len(self._load_local_aliases()),
            "index_alias_count": index_count,
        }

    def get_mailbox(self, alias_email: str) -> Dict:
        _account, meta = self.resolve_alias(alias_email)
        return meta

    def resolve_alias(self, alias_email: str) -> Tuple[Dict, Dict]:
        alias = _normalize_alias(alias_email)
        if not alias:
            raise MailboxNotFound("mailbox not found")
        for item in self.list_mailboxes():
            if item["alias_email"] == alias:
                account = self.account_mgr.get_account(item["account_id"])
                if not account:
                    raise MailboxNotFound("mailbox not found")
                return account, item
        raise MailboxNotFound("mailbox not found")

    def get_messages(self, alias_email: str, limit: int = 1, force: bool = False) -> List[Dict]:
        account, _meta = self.resolve_alias(alias_email)
        messages = self._get_messages_for_account(account, alias_email, limit, force)
        if len(messages) == 1:
            return [self._with_message_detail(account, alias_email, messages[0])]
        return messages

    def get_latest_message(self, alias_email: str, force: bool = False) -> Optional[Dict]:
        messages = self.get_messages(alias_email, limit=1, force=force)
        return messages[0] if messages else None

    def get_message_detail(self, alias_email: str, message_id: str) -> Dict:
        account, _meta = self.resolve_alias(alias_email)
        return self._get_message_detail_for_account(account, alias_email, message_id)
    def delete_mailbox(self, alias_email: str, local_only: bool = False) -> Dict:
        summary = self.delete_mailboxes([alias_email], local_only=local_only, raise_errors=True)
        return summary["results"][0]

    def delete_mailboxes(
        self,
        alias_emails: List[str],
        local_only: bool = False,
        raise_errors: bool = False,
        pause: float = REMOTE_DELETE_PAUSE_SECONDS,
    ) -> Dict:
        """按账号批量删除：每个账号最多查一次 Apple 别名表，别名数再多也不会放大请求。"""
        planned = self._plan_deletions(alias_emails, raise_errors)
        results = list(planned["failed"])
        targets = planned["targets"]
        remote_maps: Dict[str, Optional[Dict[str, str]]] = {}
        touched_accounts = set()
        remote_calls = 0

        for alias, account, mailbox in targets:
            anonymous_id = str(mailbox.get("anonymous_id") or "").strip()
            warning = ""
            if not anonymous_id and not local_only:
                # 本地索引可能是旧版本写入的（没有 anonymousId），或别名建于上次同步之后，
                # 这时必须现场向 Apple 查一次，否则删除只会清本地记录。
                acc_id = account["id"]
                if acc_id not in remote_maps:
                    remote_maps[acc_id] = self._remote_alias_map(acc_id)
                mapping = remote_maps[acc_id]
                if mapping is None or mapping.get(alias, "__missing__") == "":
                    if raise_errors:
                        raise AliasRemoteIdUnavailable(ALIAS_ID_UNRESOLVED_MESSAGE)
                    results.append({
                        "alias_email": alias,
                        "ok": False,
                        "code": "alias_id_unresolved",
                        "error": ALIAS_ID_UNRESOLVED_MESSAGE,
                    })
                    continue
                anonymous_id = mapping.get(alias, "")
                if not anonymous_id:
                    warning = "别名在 Apple 侧已不存在，仅清理本地记录"

            remote_deleted = False
            if anonymous_id and not local_only:
                if remote_calls and pause:
                    time.sleep(pause)
                remote_calls += 1
                try:
                    ok = self.account_mgr.delete_alias_for_account(
                        account["id"], anonymous_id, refresh_counts=False
                    )
                except Exception as exc:
                    if raise_errors:
                        raise
                    results.append({
                        "alias_email": alias,
                        "ok": False,
                        "code": "remote_delete_failed",
                        "error": str(exc)[:200],
                    })
                    continue
                if not ok:
                    if raise_errors:
                        raise RuntimeError("Apple 别名删除失败")
                    results.append({
                        "alias_email": alias,
                        "ok": False,
                        "code": "remote_delete_failed",
                        "error": "Apple 别名删除失败",
                    })
                    continue
                remote_deleted = True
                touched_accounts.add(account["id"])
            elif local_only and not warning:
                warning = "按请求仅清理本地记录，Apple 别名保留"

            local_removed = self._purge_local_mailbox(alias, account["id"])
            shared_revoked = False
            shared = self.shared_store.get_for_alias(alias) if self.shared_store else None
            if shared and self.shared_store:
                shared_revoked = self.shared_store.revoke(shared["id"])
            results.append({
                "alias_email": alias,
                "ok": True,
                "anonymous_id": anonymous_id,
                "remote_deleted": remote_deleted,
                "local_removed": local_removed,
                "shared_revoked": shared_revoked,
                "warning": warning,
            })

        self._refresh_account_counts(touched_accounts)
        deleted = [item for item in results if item.get("ok")]
        return {
            "results": results,
            "requested": len(targets) + len(planned["failed"]),
            "deleted": len(deleted),
            "remote_deleted": len([item for item in deleted if item.get("remote_deleted")]),
            "failed": len(results) - len(deleted),
        }

    def _plan_deletions(self, alias_emails: List[str], raise_errors: bool) -> Dict:
        targets: List[Tuple[str, Dict, Dict]] = []
        failed: List[Dict] = []
        seen = set()
        for raw in alias_emails or []:
            alias = _normalize_alias(raw)
            if not alias or alias in seen:
                continue
            seen.add(alias)
            try:
                account, mailbox = self.resolve_alias(alias)
            except MailboxNotFound:
                if raise_errors:
                    raise
                failed.append({
                    "alias_email": alias,
                    "ok": False,
                    "code": "not_found",
                    "error": "mailbox not found",
                })
                continue
            targets.append((mailbox["alias_email"], account, mailbox))
        return {"targets": targets, "failed": failed}

    def _refresh_account_counts(self, account_ids):
        refresh = getattr(self.account_mgr, "refresh_alias_counts", None)
        if not callable(refresh):
            return
        for acc_id in account_ids:
            try:
                refresh(acc_id)
            except Exception:
                pass

    def _remote_alias_map(self, account_id: str) -> Optional[Dict[str, str]]:
        """alias -> anonymousId；查询失败或列表为空时返回 None（无法判定，不可当作已删除）。"""
        fetch = getattr(self.account_mgr, "get_aliases_for_account", None)
        if not callable(fetch):
            return None
        try:
            aliases = fetch(account_id) or []
        except Exception:
            return None
        if not aliases:
            return None
        mapping: Dict[str, str] = {}
        for item in aliases:
            alias = _normalize_alias(item.get("hme") or item.get("email") or item.get("alias_email"))
            if not alias:
                continue
            mapping[alias] = str(
                item.get("anonymousId") or item.get("anonymous_id") or item.get("id") or ""
            ).strip()
        return mapping

    def _purge_local_mailbox(self, alias: str, account_id: str) -> List[str]:
        touched: List[str] = []
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                mailboxes = data.get("mailboxes") if isinstance(data, dict) else None
                if isinstance(mailboxes, dict) and alias in mailboxes:
                    del mailboxes[alias]
                    data["updated_at"] = datetime.now().isoformat()
                    self.index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    touched.append("index")
            except (OSError, json.JSONDecodeError):
                pass

        if self.latest_emails_path.exists():
            try:
                lines = self.latest_emails_path.read_text(encoding="utf-8").splitlines()
                kept = [line for line in lines if not line.strip().split("\t", 1)[0].strip().lower() == alias]
                if len(kept) != len(lines):
                    text = "\n".join(kept)
                    self.latest_emails_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
                    touched.append("latest_emails")
            except OSError:
                pass

        move = getattr(self.account_mgr, "move_mailboxes_to_group", None)
        if callable(move):
            try:
                if move([alias], DEFAULT_GROUP_ID):
                    touched.append("group")
            except Exception:
                pass

        cache = getattr(self.account_mgr, "_cache", None)
        clear_alias = getattr(cache, "clear_alias", None)
        if callable(clear_alias):
            try:
                if clear_alias(account_id, alias):
                    touched.append("mail_cache")
            except Exception:
                pass
        return touched

    def shared_public_view(self, share: Dict, force: bool = False) -> Dict:
        alias = _normalize_alias(share.get("alias_email", ""))
        account = self.account_mgr.get_account(share.get("account_id", ""))
        if not alias or not account:
            raise MailboxNotFound("mailbox not found")

        latest_items = self._get_messages_for_account(account, alias, limit=1, force=force)
        latest = latest_items[0] if latest_items else None
        message = None
        if latest:
            detail = None
            if latest.get("message_id"):
                try:
                    detail = self._get_message_detail_for_account(account, alias, latest["message_id"])
                except Exception:
                    detail = None
            body = (detail or latest).get("body") or latest.get("body_preview") or ""
            message = {
                "message_id": latest.get("message_id", ""),
                "subject": latest.get("subject", ""),
                "from": latest.get("from", ""),
                "date": latest.get("date", ""),
                "body": str(body),
            }

        cache_age = self._cache_age(account.get("id", ""))
        mailbox = self._summary_from_parts(alias, account, share)
        return {
            "mailbox": alias,
            "label": mailbox.get("label", ""),
            "message": message,
            "fetched_at": datetime.now().isoformat(),
            "cache_age_sec": cache_age,
        }

    def _get_messages_for_account(
        self, account: Dict, alias_email: str, limit: int = 1, force: bool = False
    ) -> List[Dict]:
        alias = _normalize_alias(alias_email)
        limit = max(1, min(int(limit or 1), 10))
        cached = self._cached_alias_messages(account.get("id", ""), alias)
        if cached and not force:
            return [self._message_summary(m) for m in cached[:limit]]

        mail = self._mail_client(account["id"])
        try:
            messages = mail.find_by_recipient(alias, limit=limit, days=30)
        except Exception as exc:
            _raise_mail_read_error(exc)
        finally:
            try:
                mail.disconnect()
            except Exception:
                pass
        self._store_alias_messages(account.get("id", ""), alias, messages)
        return [self._message_summary(m) for m in messages[:limit]]

    def _get_message_detail_for_account(self, account: Dict, alias_email: str, message_id: str) -> Dict:
        if not message_id:
            raise MailboxNotFound("message not found")

        known = self._get_messages_for_account(account, alias_email, limit=10, force=False)
        if known and not any(str(m.get("message_id")) == str(message_id) for m in known):
            raise MailboxNotFound("message not found")

        mail = self._mail_client(account["id"])
        try:
            full = mail.fetch_full(str(message_id).encode("utf-8"))
        except Exception as exc:
            _raise_mail_read_error(exc)
        finally:
            try:
                mail.disconnect()
            except Exception:
                pass
        if not full:
            raise MailboxNotFound("message not found")
        alias = _normalize_alias(alias_email)
        if not _message_matches_alias(full, alias):
            raise MailboxNotFound("message not found")
        full.setdefault("matched_recipient", alias)
        detail = self._message_summary(full)
        detail["body"] = str(full.get("body") or "")
        detail["content_type"] = full.get("content_type", "")
        return detail

    def _with_message_detail(self, account: Dict, alias_email: str, message: Dict) -> Dict:
        msg_id = str(message.get("message_id") or "")
        if not msg_id:
            return message
        try:
            detail = self._get_message_detail_for_account(account, alias_email, msg_id)
        except Exception:
            return message
        merged = dict(message)
        merged.update({key: value for key, value in detail.items() if value not in (None, "")})
        return merged

    def _mail_client(self, account_id: str):
        try:
            return self.account_mgr.get_mail_client(account_id)
        except KeyError:
            raise
        except Exception as exc:
            _raise_mail_read_error(exc)

    def _fetch_remote_aliases(self) -> List[Dict]:
        try:
            aliases = self.account_mgr.get_all_aliases()
        except Exception as exc:
            raise IMAPUnavailable(str(exc) or "mailbox sync unavailable") from exc
        return [dict(alias, source="remote") for alias in (aliases or [])]

    def _load_index(self) -> List[Dict]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if isinstance(data, dict):
            mailboxes = data.get("mailboxes", {})
            if isinstance(mailboxes, dict):
                return list(mailboxes.values())
            if isinstance(mailboxes, list):
                return mailboxes
        return []

    def _save_index(self, aliases: List[Dict]):
        accounts = {a.get("id"): a for a in self.account_mgr.list_accounts()}
        by_alias: Dict[str, Dict] = {}
        for alias_data in aliases:
            item = dict(alias_data)
            item.setdefault("source", "remote")
            self._merge_source(by_alias, accounts, item)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(
                {"mailboxes": by_alias, "updated_at": datetime.now().isoformat()},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _load_local_aliases(self) -> List[Dict]:
        if not self.latest_emails_path.exists():
            return []
        items = []
        try:
            lines = self.latest_emails_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for position, line in enumerate(lines):
            parts = line.strip().split("\t")
            if not parts or "@" not in parts[0]:
                continue
            item = {
                "alias_email": _normalize_alias(parts[0]),
                "account_id": parts[1] if len(parts) > 1 else "",
                "source": "local",
            }
            # 第三列是创建时间（新格式）；旧的两列记录没有时间，
            # 用追加顺序兜底，至少保证新建的排在旧的前面而不是沉底。
            created_at = _created_at_ms(parts[2]) if len(parts) > 2 else 0.0
            item["created_at"] = created_at or float(position + 1)
            item["created_at_estimated"] = not created_at
            items.append(item)
        return items

    def _merge_source(self, by_alias: Dict[str, Dict], accounts: Dict[str, Dict], source: Dict):
        alias = _normalize_alias(source.get("alias_email") or source.get("hme") or source.get("email"))
        if not alias:
            return
        account = accounts.get(source.get("account_id"), {})
        summary = self._summary_from_parts(alias, account, source)
        existing = by_alias.get(alias, {})
        merged = dict(existing)
        for key, value in summary.items():
            if key == "is_active" or value not in (None, ""):
                merged[key] = value
        # 本地文件的估算时间不得覆盖索引/远端的真实创建时间。
        if (
            source.get("created_at_estimated")
            and existing.get("created_at")
            and not existing.get("created_at_estimated")
        ):
            merged["created_at"] = existing["created_at"]
            merged["created_at_estimated"] = False
        by_alias[alias] = merged

    def _mailbox_group_fields(self, alias: str, source: Dict) -> Dict:
        getter = getattr(self.account_mgr, "get_mailbox_group", None)
        if callable(getter):
            try:
                group = getter(alias)
                return {
                    "group_id": group.get("id", DEFAULT_GROUP_ID),
                    "group_name": group.get("name", DEFAULT_GROUP_NAME),
                    "group_color": group.get("color", DEFAULT_GROUP_COLOR),
                }
            except Exception:
                pass

        group_id = source.get("group_id") or DEFAULT_GROUP_ID
        return {
            "group_id": group_id,
            "group_name": source.get("group_name") or (DEFAULT_GROUP_NAME if group_id == DEFAULT_GROUP_ID else ""),
            "group_color": source.get("group_color") or (DEFAULT_GROUP_COLOR if group_id == DEFAULT_GROUP_ID else ""),
        }

    def _summary_from_parts(self, alias: str, account: Dict, source: Dict) -> Dict:
        is_active = source.get("isActive", source.get("active", source.get("is_active", True)))
        group_fields = self._mailbox_group_fields(alias, source)
        return {
            "alias_email": alias,
            "account_id": source.get("account_id") or account.get("id", ""),
            "account_name": source.get("account_name") or account.get("name", ""),
            "label": source.get("label", ""),
            "anonymous_id": source.get("anonymousId") or source.get("anonymous_id") or source.get("id") or "",
            "forward_to_email": source.get("forwardToEmail") or source.get("forward_to_email") or "",
            **group_fields,
            "is_active": bool(is_active),
            "created_at": source.get("createTimestamp") or source.get("createdAt") or source.get("created_at") or "",
            "created_at_estimated": bool(source.get("created_at_estimated")),
            "source": source.get("source") or "local",
            "shared": None,
        }

    def _cached_alias_messages(self, account_id: str, alias: str, allow_stale: bool = False) -> List[Dict]:
        cache = getattr(self.account_mgr, "_cache", None)
        if not cache:
            return []
        try:
            age = cache.cache_age_seconds(account_id)
            cached = cache.get_alias_mail(account_id, alias)
        except Exception:
            return []
        if not cached or (not allow_stale and age >= MAIL_CACHE_TTL_SECONDS):
            return []
        return sorted(cached, key=self._message_sort_key, reverse=True)

    def _store_alias_messages(self, account_id: str, alias: str, messages: List[Dict]):
        cache = getattr(self.account_mgr, "_cache", None)
        if not cache or not messages:
            return
        try:
            cache.set_alias_mail(account_id, alias, messages)
        except Exception:
            pass

    @staticmethod
    def _message_sort_key(message: Dict):
        value = str(message.get("date") or "")
        try:
            return (1, parsedate_to_datetime(value).timestamp())
        except (TypeError, ValueError, OverflowError):
            return (0, value)

    def _latest_cached_subject(self, account_id: str, alias: str) -> str:
        cached = self._cached_alias_messages(account_id, alias, allow_stale=True)
        return str(cached[0].get("subject") or "") if cached else ""

    def _cache_age(self, account_id: str):
        cache = getattr(self.account_mgr, "_cache", None)
        if not cache:
            return None
        try:
            return cache.cache_age_seconds(account_id)
        except Exception:
            return None

    @staticmethod
    def _message_summary(message: Dict) -> Dict:
        body = str(message.get("body") or "")
        preview = str(message.get("body_preview") or body)
        otp_code = _extract_message_otp(message)
        return {
            "message_id": str(message.get("message_id") or message.get("id") or ""),
            "subject": str(message.get("subject") or ""),
            "from": str(message.get("from") or ""),
            "to": str(message.get("to") or ""),
            "matched_recipient": str(message.get("matched_recipient") or ""),
            "date": str(message.get("date") or ""),
            "body_preview": preview[:500],
            "body": body,
            "text": body,
            "otp_code": otp_code,
            "verification_code": otp_code,
            "code": otp_code,
        }



def _extract_message_otp(message: Dict) -> str:
    for key in ("otp_code", "verification_code", "code"):
        value = str(message.get(key) or "").strip()
        if value:
            return value
    text = "\n".join(
        str(message.get(key) or "")
        for key in ("subject", "body", "body_preview", "text", "content")
    )
    if not text.strip():
        return ""
    from icloud_mail import ICloudMail

    return ICloudMail.extract_verification_code(text)

def normalize_mailbox_sort(sort: str) -> str:
    raw = str(sort or "").strip().lower()
    if raw in ("created_at_asc", "oldest", "time_asc", "created_asc"):
        return "created_at_asc"
    if raw in ("alias", "email", "hme", "address"):
        return "alias"
    return "created_at"


def _created_at_ms(value) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return 0.0
        if number < 1e12:
            return number * 1000.0
        return number
    text = str(value).strip()
    if not text:
        return 0.0
    if text.isdigit():
        return _created_at_ms(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return parsed.timestamp() * 1000.0


def _mailbox_sort_key(item: Dict, sort: str):
    alias = str(item.get("alias_email") or "")
    created = _created_at_ms(item.get("created_at"))
    if sort == "alias":
        return (alias, -created)
    if sort == "created_at_asc":
        return (created, alias)
    return (-created, alias)


def _normalize_alias(alias_email: str) -> str:
    return str(alias_email or "").strip().lower()



def _message_matches_alias(message: Dict, alias: str) -> bool:
    alias = _normalize_alias(alias)
    if not alias:
        return False
    if _normalize_alias(message.get("matched_recipient", "")) == alias:
        return True
    haystack = "\n".join(
        str(message.get(key) or "")
        for key in ("recipient_headers", "to")
        if message.get(key)
    )
    addresses = {addr.lower() for _name, addr in getaddresses([haystack]) if addr}
    return alias in addresses or alias in haystack.lower()