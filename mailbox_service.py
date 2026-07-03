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

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from account_manager import AccountManager, LATEST_EMAILS
from shared_mailboxes import SharedMailboxStore


class MailboxNotFound(KeyError):
    """Mailbox alias was not found in local or remote alias indexes."""


class IMAPNotConfigured(ValueError):
    """The owning account has no iCloud IMAP address or app-specific password."""


class IMAPUnavailable(RuntimeError):
    """The IMAP backend failed while reading mail."""


class MailboxService:
    """Read-only service for HME mailbox lookup and IMAP mail access."""

    def __init__(
        self,
        account_mgr: AccountManager,
        shared_store: Optional[SharedMailboxStore] = None,
        latest_emails_path: Path = LATEST_EMAILS,
    ):
        self.account_mgr = account_mgr
        self.shared_store = shared_store
        self.latest_emails_path = Path(latest_emails_path)

    def list_mailboxes(
        self, q: str = "", account_id: str = "", status: str = ""
    ) -> List[Dict]:
        accounts = {a.get("id"): a for a in self.account_mgr.list_accounts()}
        by_alias: Dict[str, Dict] = {}

        for local in self._load_local_aliases():
            alias = _normalize_alias(local.get("alias_email"))
            if not alias:
                continue
            acc = accounts.get(local.get("account_id"), {})
            by_alias[alias] = self._summary_from_parts(alias, acc, local)

        try:
            remote_aliases = self.account_mgr.get_all_aliases()
        except Exception:
            remote_aliases = []
        for remote in remote_aliases:
            alias = _normalize_alias(remote.get("hme") or remote.get("email"))
            if not alias:
                continue
            acc = accounts.get(remote.get("account_id"), {})
            base = by_alias.get(alias, {})
            merged = dict(base)
            merged.update(self._summary_from_parts(alias, acc, remote))
            by_alias[alias] = merged

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
        if status:
            normalized = status.lower()
            if normalized in ("active", "enabled"):
                items = [item for item in items if item.get("is_active") is True]
            elif normalized in ("inactive", "disabled", "revoked"):
                items = [item for item in items if item.get("is_active") is False]

        return sorted(items, key=lambda item: item.get("created_at") or item["alias_email"], reverse=True)

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
        limit = max(1, min(int(limit or 1), 10))
        mail = self._mail_client(account["id"])
        try:
            messages = mail.find_by_recipient(_normalize_alias(alias_email), limit=limit, days=30)
        except ValueError as exc:
            raise IMAPNotConfigured(str(exc)) from exc
        except Exception as exc:
            raise IMAPUnavailable(str(exc) or "IMAP unavailable") from exc
        finally:
            try:
                mail.disconnect()
            except Exception:
                pass
        return [self._message_summary(m) for m in messages[:limit]]

    def get_latest_message(self, alias_email: str, force: bool = False) -> Optional[Dict]:
        messages = self.get_messages(alias_email, limit=1, force=force)
        return messages[0] if messages else None

    def get_message_detail(self, alias_email: str, message_id: str) -> Dict:
        account, _meta = self.resolve_alias(alias_email)
        if not message_id:
            raise MailboxNotFound("message not found")

        known = self.get_messages(alias_email, limit=10, force=False)
        if known and not any(str(m.get("message_id")) == str(message_id) for m in known):
            raise MailboxNotFound("message not found")

        mail = self._mail_client(account["id"])
        try:
            full = mail.fetch_full(str(message_id).encode("utf-8"))
        except ValueError as exc:
            raise IMAPNotConfigured(str(exc)) from exc
        except Exception as exc:
            raise IMAPUnavailable(str(exc) or "IMAP unavailable") from exc
        finally:
            try:
                mail.disconnect()
            except Exception:
                pass
        if not full:
            raise MailboxNotFound("message not found")
        if _normalize_alias(alias_email) not in str(full.get("to", "")).lower():
            raise MailboxNotFound("message not found")
        detail = self._message_summary(full)
        detail["body"] = str(full.get("body") or "")
        detail["content_type"] = full.get("content_type", "")
        return detail

    def shared_public_view(self, share: Dict, force: bool = False) -> Dict:
        alias = share.get("alias_email", "")
        mailbox = self.get_mailbox(alias)
        latest = self.get_latest_message(alias, force=force)
        message = None
        if latest:
            detail = None
            if latest.get("message_id"):
                try:
                    detail = self.get_message_detail(alias, latest["message_id"])
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
        account_id = mailbox.get("account_id")
        cache_age = None
        if account_id and hasattr(self.account_mgr, "_cache"):
            try:
                cache_age = self.account_mgr._cache.cache_age_seconds(account_id)
            except Exception:
                cache_age = None
        return {
            "mailbox": mailbox.get("alias_email", alias),
            "label": mailbox.get("label", ""),
            "message": message,
            "fetched_at": datetime.now().isoformat(),
            "cache_age_sec": cache_age,
        }

    def _mail_client(self, account_id: str):
        try:
            return self.account_mgr.get_mail_client(account_id)
        except ValueError as exc:
            raise IMAPNotConfigured(str(exc)) from exc
        except KeyError:
            raise
        except Exception as exc:
            raise IMAPUnavailable(str(exc) or "IMAP unavailable") from exc

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
            })
        return items

    def _summary_from_parts(self, alias: str, account: Dict, source: Dict) -> Dict:
        is_active = source.get("isActive", source.get("active", True))
        return {
            "alias_email": alias,
            "account_id": source.get("account_id") or account.get("id", ""),
            "account_name": source.get("account_name") or account.get("name", ""),
            "label": source.get("label", ""),
            "is_active": bool(is_active),
            "created_at": source.get("createTimestamp") or source.get("createdAt") or source.get("created_at") or "",
            "shared": None,
        }

    @staticmethod
    def _message_summary(message: Dict) -> Dict:
        body = str(message.get("body") or message.get("body_preview") or "")
        return {
            "message_id": str(message.get("message_id") or message.get("id") or ""),
            "subject": str(message.get("subject") or ""),
            "from": str(message.get("from") or ""),
            "to": str(message.get("to") or ""),
            "date": str(message.get("date") or ""),
            "body_preview": body[:200],
            "body": str(message.get("body") or ""),
        }


def _normalize_alias(alias_email: str) -> str:
    return str(alias_email or "").strip().lower()
