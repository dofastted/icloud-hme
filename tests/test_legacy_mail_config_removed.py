from pathlib import Path


USER_FACING_FILES = [
    "README.md",
    "account_manager.py",
    "web_ui.py",
    "static/js/02-accounts.js",
    "static/js/05-inbox-docs.js",
]


LEGACY_TEXT = [
    "未设置 App 专用密码",
    "App 专用密码",
    "应用密码",
    "IMAP 未配置",
    "IMAP 已配置",
    "设置 IMAP",
    "app-password",
    "/api/v1/accounts/{id}/imap",
]


def test_legacy_mail_config_text_is_not_user_facing():
    root = Path(__file__).resolve().parents[1]
    combined = "\n".join((root / rel).read_text(encoding="utf-8") for rel in USER_FACING_FILES)

    for text in LEGACY_TEXT:
        assert text not in combined
