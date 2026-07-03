#!/usr/bin/env python3
"""Shared mailbox key store.

Only SHA-256 digests are persisted. The raw shared key is returned once from
``create`` and cannot be recovered from disk.
"""

import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
SHARED_MAILBOXES_FILE = HERE / "shared_mailboxes.json"
KEY_PREFIX = "shk_"


class SharedMailboxStore:
    """JSON-backed store for public read-only mailbox shares."""

    def __init__(self, path: Path = SHARED_MAILBOXES_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._shares: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        with self._lock:
            if not self.path.exists():
                self._shares = {}
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._shares = data.get("shares", {}) if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                self._shares = {}

    def _save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {"shares": self._shares, "updated_at": _now()},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def create(self, account_id: str, alias_email: str) -> Dict:
        alias = _normalize_alias(alias_email)
        if not account_id:
            raise ValueError("account_id is required")
        if not alias:
            raise ValueError("alias_email is required")

        with self._lock:
            if self.get_for_alias(alias):
                raise ValueError("shared mailbox already exists for alias")

            raw_key = KEY_PREFIX + secrets.token_urlsafe(24)
            share_id = "shr_" + secrets.token_hex(4)
            while share_id in self._shares:
                share_id = "shr_" + secrets.token_hex(4)
            record = {
                "id": share_id,
                "account_id": account_id,
                "alias_email": alias,
                "prefix": raw_key[:12],
                "sha256": _digest(raw_key),
                "active": True,
                "created_at": _now(),
                "revoked_at": None,
                "last_accessed_at": None,
                "access_count": 0,
            }
            self._shares[share_id] = record
            self._save()

        public = self._public_record(record)
        public["share_key"] = raw_key
        return public

    def verify(self, raw_key: str) -> Optional[Dict]:
        if not raw_key:
            return None
        digest = _digest(raw_key.strip())
        with self._lock:
            for record in self._shares.values():
                if not record.get("active", True):
                    continue
                if hmac.compare_digest(record.get("sha256", ""), digest):
                    record["last_accessed_at"] = _now()
                    record["access_count"] = int(record.get("access_count") or 0) + 1
                    self._save()
                    return self._public_record(record)
        return None

    def revoke(self, share_id: str) -> bool:
        with self._lock:
            record = self._shares.get(share_id)
            if not record:
                return False
            record["active"] = False
            record["revoked_at"] = _now()
            self._save()
            return True

    def list(self, include_revoked: bool = True) -> List[Dict]:
        with self._lock:
            records = list(self._shares.values())
        if not include_revoked:
            records = [r for r in records if r.get("active", True)]
        return [self._public_record(r) for r in sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)]

    def get(self, share_id: str) -> Optional[Dict]:
        with self._lock:
            record = self._shares.get(share_id)
            return self._public_record(record) if record else None

    def get_for_alias(self, alias_email: str) -> Optional[Dict]:
        alias = _normalize_alias(alias_email)
        with self._lock:
            for record in self._shares.values():
                if not record.get("active", True):
                    continue
                if record.get("alias_email") == alias:
                    return self._public_record(record)
        return None

    @staticmethod
    def _public_record(record: Dict) -> Dict:
        return {
            "id": record.get("id", ""),
            "account_id": record.get("account_id", ""),
            "alias_email": record.get("alias_email", ""),
            "prefix": record.get("prefix", ""),
            "active": bool(record.get("active", True)),
            "created_at": record.get("created_at"),
            "revoked_at": record.get("revoked_at"),
            "last_accessed_at": record.get("last_accessed_at"),
            "access_count": int(record.get("access_count") or 0),
        }


def _digest(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _normalize_alias(alias_email: str) -> str:
    return str(alias_email or "").strip().lower()


def _now() -> str:
    return datetime.now().isoformat()
