#!/usr/bin/env python3
"""本地管理员口令。

只落盘 scrypt 哈希。文件已存在时绝不重置，避免把改过的口令盖回默认值。
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("HME_DATA_DIR", str(HERE)))
ADMIN_FILE = DATA_DIR / "admin.json"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "123456qwe"
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
_DUMMY_SALT = b"\x00" * 16


class AdminAuthStore:
    """管理员凭证。safe for concurrent calls."""

    def __init__(self, path: Path = ADMIN_FILE):
        self.path = path
        self._lock = threading.RLock()

    def verify(self, username: str, password: str) -> bool:
        username = username if isinstance(username, str) else ""
        password = password if isinstance(password, str) else ""
        with self._lock:
            record = self._load_or_seed()
            salt = _salt_of(record)
            digest = _scrypt(password, salt)
            if not record:
                return False
            user_ok = _equals(username.encode("utf-8"), str(record["username"]).encode("utf-8"))
            pass_ok = _equals(digest, bytes.fromhex(record["password_hash"]))
            return user_ok and pass_ok

    def _load_or_seed(self) -> Optional[dict]:
        if not self.path.exists():
            record = _record(DEFAULT_USERNAME, DEFAULT_PASSWORD)
            self._write(record)
            return record
        return _parse(self._read_text())

    def _read_text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _write(self, record: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(record, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, self.path)


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )


def _record(username: str, password: str) -> dict:
    salt = secrets.token_bytes(16)
    return {
        "username": username,
        "salt": salt.hex(),
        "password_hash": _scrypt(password, salt).hex(),
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
    }


def _parse(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    username = data.get("username")
    salt = data.get("salt")
    password_hash = data.get("password_hash")
    if not isinstance(username, str) or not username:
        return None
    if not isinstance(salt, str) or not isinstance(password_hash, str):
        return None
    if data.get("n") != SCRYPT_N or data.get("r") != SCRYPT_R or data.get("p") != SCRYPT_P:
        return None
    try:
        salt_bytes = bytes.fromhex(salt)
        hash_bytes = bytes.fromhex(password_hash)
    except ValueError:
        return None
    if len(salt_bytes) != 16 or len(hash_bytes) != SCRYPT_DKLEN:
        return None
    return data


def _salt_of(record: Optional[dict]) -> bytes:
    if not record:
        return _DUMMY_SALT
    return bytes.fromhex(record["salt"])


def _equals(left: bytes, right: bytes) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)
