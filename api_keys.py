#!/usr/bin/env python3
"""本地 API Key 管理。

只持久化 SHA-256 摘要；明文 key 仅在创建时返回一次。
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
API_KEYS_FILE = HERE / "api_keys.json"
KEY_PREFIX = "hme_"


class APIKeyStore:
    """API Key 存储。safe for concurrent calls."""

    def __init__(self, path: Path = API_KEYS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._keys: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        with self._lock:
            if not self.path.exists():
                self._keys = {}
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._keys = data.get("keys", {}) if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                self._keys = {}

    def _save(self):
        with self._lock:
            self.path.write_text(
                json.dumps({"keys": self._keys, "updated_at": _now()}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def has_keys(self) -> bool:
        with self._lock:
            return any(k.get("active", True) for k in self._keys.values())

    def create(self, name: str = "default") -> Dict:
        raw_key = KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = "key_" + secrets.token_hex(8)
        record = {
            "id": key_id,
            "name": name or "default",
            "prefix": raw_key[:12],
            "sha256": _digest(raw_key),
            "active": True,
            "created_at": _now(),
            "last_used_at": None,
        }
        with self._lock:
            self._keys[key_id] = record
            self._save()
        public = self._public_record(record)
        public["api_key"] = raw_key
        return public

    def list(self) -> List[Dict]:
        with self._lock:
            return [self._public_record(k) for k in self._keys.values()]

    def verify(self, raw_key: str) -> Optional[Dict]:
        if not raw_key:
            return None
        digest = _digest(raw_key.strip())
        with self._lock:
            for record in self._keys.values():
                if not record.get("active", True):
                    continue
                if hmac.compare_digest(record.get("sha256", ""), digest):
                    record["last_used_at"] = _now()
                    self._save()
                    return self._public_record(record)
        return None

    def deactivate(self, key_id: str) -> bool:
        with self._lock:
            record = self._keys.get(key_id)
            if not record:
                return False
            record["active"] = False
            record["revoked_at"] = _now()
            self._save()
            return True

    @staticmethod
    def _public_record(record: Dict) -> Dict:
        return {
            "id": record.get("id", ""),
            "name": record.get("name", ""),
            "prefix": record.get("prefix", ""),
            "active": bool(record.get("active", True)),
            "created_at": record.get("created_at"),
            "last_used_at": record.get("last_used_at"),
            "revoked_at": record.get("revoked_at"),
        }


def extract_api_key(headers) -> str:
    header_value = headers.get("Authorization", "")
    if header_value.lower().startswith("bearer "):
        return header_value.split(" ", 1)[1].strip()
    return headers.get("X-API-Key", "").strip()


def _digest(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now().isoformat()
