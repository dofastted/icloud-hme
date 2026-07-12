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
from email.utils import getaddresses
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from account_manager import AccountManager, LATEST_EMAILS, DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, DEFAULT_GROUP_COLOR
from shared_mailboxes import SharedMailboxStore

MAILBOX_INDEX_FILE = LATEST_EMAILS.parent / "mailbox_index.json"
MAIL_CACHE_TTL_SECONDS = 300
MAIL_READ_UNAVAILABLE_MESSAGE = "邮件读取暂不可用"


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


class MailboxService:
    """Read-only service for HME mailbox lookup and IMAP mail access."""

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
        self, q: str = "", account_id: str = "", group_id: str = "", status: str = "", refresh: bool = False
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

        needle = q.strip().lower()
        if needle:
            items = [
                item for item in items
                if needle in item["alias_email"].lower()
                or needle in str(item.get("label") or "").lower()
                or needle in str(item.get("account_name") or "").lower()
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

        return sorted(items, key=_sort_key, reverse=True)

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
        for line in lines:
            parts = line.strip().split("\t")
            if not parts or "@" not in parts[0]:
                continue
            items.append({
                "alias_email": _normalize_alias(parts[0]),
                "account_id": parts[1] if len(parts) > 1 else "",
                "source": "local",
            })
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
            "forward_to_email": source.get("forwardToEmail") or source.get("forward_to_email") or "",
            **group_fields,
            "is_active": bool(is_active),
            "created_at": source.get("createTimestamp") or source.get("createdAt") or source.get("created_at") or "",
            "source": source.get("source") or "local",
            "shared": None,
        }

    def _cached_alias_messages(self, account_id: str, alias: str) -> List[Dict]:
        cache = getattr(self.account_mgr, "_cache", None)
        if not cache:
            return []
        try:
            age = cache.cache_age_seconds(account_id)
            cached = cache.get_alias_mail(account_id, alias)
        except Exception:
            return []
        if cached and age < MAIL_CACHE_TTL_SECONDS:
            return list(cached)
        return []

    def _store_alias_messages(self, account_id: str, alias: str, messages: List[Dict]):
        cache = getattr(self.account_mgr, "_cache", None)
        if not cache or not messages:
            return
        try:
            cache.set_alias_mail(account_id, alias, messages)
        except Exception:
            pass

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

def _sort_key(item: Dict) -> str:
    return f"{item.get('created_at') or ''}|{item.get('alias_email') or ''}"


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