#!/usr/bin/env python3
"""
iCloud Mail — IMAP 收件箱检查模块
===================================
通过 iCloud Mail 认证凭据连接 IMAP，
查询隐私邮箱别名收到的邮件。

用法:
    from icloud_mail import ICloudMail

    mail = ICloudMail("user@icloud.com", "app-specific-password")
    emails = mail.check_inbox(limit=20)
    # 查找某个隐私别名收到的邮件
    alias_mail = mail.find_by_recipient("alias@icloud.com")

前提:
  - 需要可用于 iCloud Mail 的认证凭据
  - iCloud 邮箱已开启邮件访问
"""

import imaplib
import email
import re
import time
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Optional, Dict, List

IMAP_SERVER = "imap.mail.me.com"
IMAP_PORT = 993
IMAP_TIMEOUT = 20
IMAP_CLIENT_ID = '("name" "iCloud HME" "version" "1.0")'
JUNK_NAME_HINTS = ("junk", "spam", "bulk", "垃圾")


CODE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{4,8})(?!\d)"),
    re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{4,8})(?![A-Za-z0-9])", re.IGNORECASE),
]


class ICloudMail:
    """iCloud Mail IMAP 客户端"""

    def __init__(self, apple_id: str, app_password: str, verbose: bool = False,
                 server: str = IMAP_SERVER, port: int = IMAP_PORT):
        self.apple_id = apple_id
        self.app_password = app_password
        self.server = server or IMAP_SERVER
        self.port = int(port or IMAP_PORT)
        self.verbose = verbose
        self._conn: Optional[imaplib.IMAP4_SSL] = None

    def connect(self) -> bool:
        try:
            self._conn = imaplib.IMAP4_SSL(self.server, self.port, timeout=IMAP_TIMEOUT)
            self._conn.login(self.apple_id, self.app_password)
            self._send_client_id()
            if self.verbose:
                print(f"[IMAP] Connected as {self.apple_id} via {self.server}:{self.port}")
            return True
        except imaplib.IMAP4.error as e:
            msg = str(e).lower()
            if "authentication" in msg or "login" in msg or "password" in msg or "[auth" in msg:
                raise RuntimeError("邮件登录认证失败，请更新邮件登录配置")
            raise RuntimeError("邮件服务器连接失败，请检查 IMAP 服务器和端口")
        except Exception as e:
            raise RuntimeError("邮件服务器连接失败，请检查 IMAP 服务器和端口") from e

    def _send_client_id(self):
        if not self._conn:
            return
        capabilities = getattr(self._conn, "capabilities", ()) or ()
        has_id = any((cap.decode() if isinstance(cap, bytes) else str(cap)).upper() == "ID" for cap in capabilities)
        if not has_id:
            return
        imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
        try:
            self._conn._simple_command("ID", IMAP_CLIENT_ID)
        except Exception:
            pass

    def disconnect(self):
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.state == "SELECTED"

    def _ensure_connected(self):
        if not self._conn:
            self.connect()
        if self._conn.state != "SELECTED":
            self._select_mailbox("INBOX")

    def _select_mailbox(self, mailbox: str = "INBOX") -> int:
        if not self._conn:
            self.connect()
        status, data = self._conn.select(mailbox or "INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(self._imap_error(f"无法选中 {mailbox or 'INBOX'}", data))
        try:
            return int(data[0]) if data else 0
        except (TypeError, ValueError):
            return 0

    def _mailboxes_to_search(self, include_junk: bool = True) -> List[str]:
        mailboxes = ["INBOX"]
        if include_junk:
            for mailbox in self._junk_mailboxes():
                if mailbox and mailbox not in mailboxes:
                    mailboxes.append(mailbox)
        return mailboxes

    def _junk_mailboxes(self) -> List[str]:
        if not self._conn:
            self.connect()
        status, data = self._conn.list()
        if status != "OK":
            return []
        result: List[str] = []
        for raw in data or []:
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            mailbox = self._parse_list_mailbox(text)
            flags = text.split(")", 1)[0].lower()
            lowered = mailbox.lower()
            if "\\junk" in flags or any(hint in lowered for hint in JUNK_NAME_HINTS):
                result.append(mailbox)
        return result

    @staticmethod
    def _parse_list_mailbox(text: str) -> str:
        match = re.search(r'\)\s+"[^"]*"\s+(.+)$', text or "")
        if not match:
            return ""
        name = match.group(1).strip()
        if name.startswith('"') and name.endswith('"'):
            return name[1:-1].replace('\\"', '"')
        return name

    @staticmethod
    def _imap_error(prefix: str, data) -> str:
        if not data:
            return prefix
        parts = []
        for item in data:
            parts.append(item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item))
        detail = "; ".join(part for part in parts if part)
        return f"{prefix}: {detail}" if detail else prefix

    def check_inbox(self, limit: int = 50, days: int = 7, include_junk: bool = True) -> List[Dict]:
        if not self._conn:
            self.connect()
        return self._search_mailboxes(None, limit, days, include_junk)

    def check_unread(self, limit: int = 50, days: int = 7, include_junk: bool = True) -> List[Dict]:
        if not self._conn:
            self.connect()
        return self._search_mailboxes("UNSEEN", limit, days, include_junk)

    def find_by_recipient(self, recipient: str, limit: int = 20, days: int = 30, include_junk: bool = True) -> List[Dict]:
        if not self._conn:
            self.connect()
        try:
            return self._search_mailboxes(f'TO "{recipient}"', limit, days, include_junk)
        except Exception:
            all_msgs = self._search_mailboxes(None, limit * 3, days, include_junk)
            return [m for m in all_msgs if recipient.lower() in m.get("to", "").lower()][:limit]

    def find_verification_codes(self, recipient: str = "",
                                limit: int = 10, days: int = 1) -> List[Dict]:
        """从最近邮件中提取验证码。"""
        self._ensure_connected()
        search_limit = max(limit * 3, 20)
        if recipient:
            headers = self.find_by_recipient(recipient, search_limit, days)
        else:
            headers = self.check_inbox(search_limit, days)
        codes: List[Dict] = []
        for header in headers:
            msg_id = str(header.get("id", "")).encode()
            full = self.fetch_full(msg_id) if msg_id else None
            body = (full or {}).get("body", "")
            text = "\n".join([header.get("subject", ""), body])
            code = self.extract_verification_code(text)
            if not code:
                continue
            item = dict(header)
            item["code"] = code
            item["body_preview"] = body[:300]
            codes.append(item)
            if len(codes) >= limit:
                break
        return codes

    @staticmethod
    def extract_verification_code(text: str) -> str:
        if not text:
            return ""
        lowered = text.lower()
        has_hint = any(word in lowered for word in (
            "code", "verification", "verify", "otp", "pin",
            "验证码", "校验码", "验证", "一次性",
        ))
        for pattern in CODE_PATTERNS:
            for match in pattern.finditer(text):
                code = match.group(1).strip()
                if code.isalpha():
                    continue
                if has_hint or code.isdigit():
                    return code
        return ""

    def stream_inbox(self, limit: int = 50, days: int = 7, include_junk: bool = True):
        if not self._conn:
            self.connect()
        for mailbox in self._mailboxes_to_search(include_junk):
            since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            full = f'(SINCE "{since}")'
            try:
                self._select_mailbox(mailbox)
                status, data = self._conn.uid("SEARCH", None, full)
            except Exception:
                continue
            if status != "OK" or not data[0]:
                continue
            uids = data[0].split()
            recent = uids[-limit:] if len(uids) > limit else uids
            for uid in reversed(recent):
                try:
                    msg = self._fetch_headers_uid(uid, mailbox)
                    if msg:
                        yield msg
                except Exception:
                    continue

    def _search_mailboxes(self, criteria: Optional[str], limit: int, days: int,
                          include_junk: bool = True) -> List[Dict]:
        emails: List[Dict] = []
        for mailbox in self._mailboxes_to_search(include_junk):
            try:
                emails.extend(self._search_and_fetch(criteria, limit, days, mailbox))
            except Exception:
                if mailbox == "INBOX":
                    raise
                continue
        emails.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
        return emails[:limit]

    def _search_and_fetch(self, criteria: Optional[str], limit: int, days: int,
                          mailbox: str = "INBOX") -> List[Dict]:
        self._select_mailbox(mailbox)
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        full = f'({criteria} SINCE "{since}")' if criteria else f'(SINCE "{since}")'
        status, data = self._conn.uid("SEARCH", None, full)
        if status != "OK" or not data[0]:
            return []
        uids = data[0].split()
        recent = uids[-limit:] if len(uids) > limit else uids
        emails: List[Dict] = []
        for uid in reversed(recent):
            try:
                msg = self._fetch_headers_uid(uid, mailbox)
                if msg:
                    emails.append(msg)
            except Exception:
                continue
        return emails

    def _fetch_headers(self, msg_id: bytes) -> Optional[Dict]:
        status, data = self._conn.fetch(msg_id, "(BODY.PEEK[HEADER])")
        if status != "OK":
            return None
        return self._parse_header_response(data, msg_id)

    def _fetch_headers_uid(self, uid: bytes, mailbox: str = "INBOX") -> Optional[Dict]:
        status, data = self._conn.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
        if status != "OK":
            return None
        return self._parse_header_response(data, uid, mailbox)

    def _parse_header_response(self, data, msg_id: bytes, mailbox: str = "INBOX") -> Optional[Dict]:
        raw = self._extract_body(data)
        if not raw:
            return None
        try:
            msg = email.message_from_bytes(raw + b"\r\n\r\n")
        except Exception:
            return None
        return {
            "id": self._format_message_id(mailbox, msg_id),
            "mailbox": mailbox,
            "from": self._decode_header(msg.get("From", "")),
            "to": self._decode_header(msg.get("To", "")),
            "subject": self._decode_header(msg.get("Subject", "")),
            "date": self._safe_date(msg.get("Date", "")),
            "body_preview": "",
            "size": len(raw),
        }

    @staticmethod
    def _format_message_id(mailbox: str, uid: bytes) -> str:
        uid_text = uid.decode() if isinstance(uid, bytes) else str(uid)
        if (mailbox or "INBOX").upper() == "INBOX":
            return uid_text
        return f"{mailbox}:{uid_text}"

    @staticmethod
    def _split_message_id(msg_id: bytes) -> tuple[str, bytes]:
        text = msg_id.decode("utf-8", errors="replace") if isinstance(msg_id, bytes) else str(msg_id)
        if ":" in text:
            mailbox, uid = text.rsplit(":", 1)
            if mailbox and uid:
                return mailbox, uid.encode("utf-8")
        return "INBOX", text.encode("utf-8")

    def fetch_body(self, msg_id: bytes) -> Optional[str]:
        mailbox, uid = self._split_message_id(msg_id)
        self._select_mailbox(mailbox)
        status, data = self._conn.uid("FETCH", uid, "(BODY.PEEK[TEXT])")
        if status != "OK":
            return None
        raw = self._extract_body(data)
        if not raw:
            return None
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return raw.decode("latin-1", errors="replace")

    def fetch_full(self, msg_id: bytes) -> Optional[Dict]:
        mailbox, uid = self._split_message_id(msg_id)
        self._select_mailbox(mailbox)
        msg = self._fetch_full_message(uid)
        if not msg:
            return None
        hdr = self._fetch_headers_uid(uid, mailbox)
        if hdr:
            msg.update(hdr)
        return msg

    def _fetch_full_message(self, msg_id: bytes) -> Optional[Dict]:
        status, data = self._conn.uid("FETCH", msg_id, "(BODY.PEEK[])")
        if status != "OK":
            status, data = self._conn.uid("FETCH", msg_id, "(RFC822)")
            if status != "OK":
                return None
        raw = self._extract_body(data)
        if not raw:
            return None
        try:
            em = email.message_from_bytes(raw)
        except Exception:
            return None

        body = ""
        html_body = ""
        if em.is_multipart():
            for part in em.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                ctype = part.get_content_type().split(';')[0].strip().lower()
                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    if ctype == "text/plain" and not body:
                        body = text
                    elif ctype == "text/html" and not html_body:
                        html_body = text
                except Exception:
                    pass
        else:
            try:
                payload = em.get_payload(decode=True)
                if payload:
                    charset = em.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    ctype = em.get_content_type().split(';')[0].strip().lower()
                    if ctype == "text/plain":
                        body = text
                    elif ctype == "text/html":
                        html_body = text
                    else:
                        body = text
            except Exception:
                pass

        if not body and html_body:
            body = _strip_html(html_body)

        return {"body": body[:5000], "content_type": em.get_content_type()}

    @staticmethod
    def _extract_body(data: list) -> Optional[bytes]:
        for item in data:
            if isinstance(item, tuple):
                for sub in item:
                    if isinstance(sub, bytes) and len(sub) > 500:
                        return sub
        for item in data:
            if isinstance(item, bytes) and len(item) > 500:
                return item
        best = None
        for item in data:
            if isinstance(item, bytes):
                if best is None or len(item) > len(best):
                    best = item
            elif isinstance(item, tuple):
                for sub in item:
                    if isinstance(sub, bytes):
                        if best is None or len(sub) > len(best):
                            best = sub
        return best if best and len(best) > 100 else None

    @staticmethod
    def _decode_header(value: str) -> str:
        if not value:
            return ""
        parts = decode_header(value)
        result = []
        for part, charset in parts:
            if isinstance(part, bytes):
                try:
                    result.append(part.decode(charset or "utf-8", errors="replace"))
                except Exception:
                    result.append(part.decode("utf-8", errors="replace"))
            else:
                result.append(str(part))
        return " ".join(result)

    def test_connection(self) -> Dict:
        try:
            self.connect()
            msg_count = self._select_mailbox("INBOX")
            return {"ok": True, "email": self.apple_id, "server": self.server, "port": self.port, "inbox_count": msg_count}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
        finally:
            try:
                self.disconnect()
            except Exception:
                pass

    @staticmethod
    def _safe_date(date_str: str) -> str:
        try:
            return parsedate_to_datetime(date_str).isoformat()
        except Exception:
            return date_str


def _strip_html(html: str) -> str:
    import re
    html = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</?(div|p|h[1-6]|li|tr|article|section|header|footer|blockquote|pre|table|hr)[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r'\2 (\1)', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<li[^>]*>', '• ', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    import html as _html
    text = _html.unescape(html)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'^\s+', '', text, flags=re.MULTILINE)
    return text.strip()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python icloud_mail.py <apple_id> <app_password> [alias_email]")
        sys.exit(1)
    apple_id, app_pwd = sys.argv[1], sys.argv[2]
    alias = sys.argv[3] if len(sys.argv) > 3 else None
    mail = ICloudMail(apple_id, app_pwd, verbose=True)
    result = mail.test_connection()
    if result["ok"]:
        print(f"连接成功! 收件箱: {result['inbox_count']} 封")
    else:
        print(f"连接失败: {result['error']}")
        sys.exit(1)
    if alias:
        msgs = mail.find_by_recipient(alias, limit=10)
        for m in msgs:
            print(f"  [{m['date'][:19]}] {m['from'][:30]} | {m['subject'][:40]}")
    else:
        msgs = mail.check_inbox(limit=10)
        for m in msgs:
            print(f"  [{m['date'][:19]}] {m['from'][:30]} | {m['subject'][:40]}")
    mail.disconnect()