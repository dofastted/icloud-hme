#!/usr/bin/env python3
"""回归测试 — 覆盖核心流程，发现重构中的破坏性变更。"""

import sys
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def test_parse_cookie_header_string():
    """Cookie Header String 格式解析"""
    from account_manager import AccountManager
    mgr = AccountManager()
    
    raw = "X_APPLE_WEB_KB=abc123; SESSION_TOKEN=xyz789"
    cookies = mgr.parse_cookie_input(raw)
    assert len(cookies) == 2
    assert cookies["X_APPLE_WEB_KB"] == "abc123"
    assert cookies["SESSION_TOKEN"] == "xyz789"
    print("  PASS test_parse_cookie_header_string")


def test_parse_cookie_json():
    """JSON 格式 Cookie 解析"""
    from account_manager import AccountManager
    mgr = AccountManager()
    
    raw = '{"X_APPLE_WEB_KB":"abc123","SESSION_TOKEN":"xyz789"}'
    cookies = mgr.parse_cookie_input(raw)
    assert len(cookies) == 2
    assert cookies["X_APPLE_WEB_KB"] == "abc123"
    print("  PASS test_parse_cookie_json")


def test_parse_empty_input():
    """空输入应抛出 ValueError"""
    from account_manager import AccountManager
    mgr = AccountManager()
    
    try:
        mgr.parse_cookie_input("")
        assert False, "应该抛出 ValueError"
    except ValueError:
        pass
    print("  PASS test_parse_empty_input")


def test_derive_icloud_email_primary():
    """dsInfo 有 primaryEmail 时直接使用"""
    from account_manager import AccountManager
    info = {"appleId": "user@qq.com", "primaryEmail": "user@icloud.com"}
    result = AccountManager._derive_icloud_email(info)
    assert result == "user@icloud.com"
    print("  PASS test_derive_icloud_email_primary")


def test_derive_icloud_email_appleid_is_icloud():
    """appleId 本身是 @icloud.com"""
    from account_manager import AccountManager
    info = {"appleId": "user@icloud.com", "primaryEmail": ""}
    result = AccountManager._derive_icloud_email(info)
    assert result == "user@icloud.com"
    print("  PASS test_derive_icloud_email_appleid_is_icloud")


def test_derive_icloud_email_third_party():
    """appleId 是第三方邮箱时推导"""
    from account_manager import AccountManager
    info = {"appleId": "test@gmail.com", "primaryEmail": ""}
    result = AccountManager._derive_icloud_email(info)
    assert result == "test@icloud.com"
    print("  PASS test_derive_icloud_email_third_party")


def test_mail_cache_basic():
    """邮件缓存基本读写"""
    from mail_cache import MailCache
    cache = MailCache()
    acc_id = "test_regressions_mail_cache"
    cache.clear_account(acc_id)
    
    try:
        cache.set_inbox(acc_id, [
            {"id": "1", "from": "a@b.com", "to": "x@icloud.com", "subject": "Hello", "date": "2025-01-01T00:00:00"},
            {"id": "2", "from": "c@d.com", "to": "y@icloud.com", "subject": "World", "date": "2025-01-02T00:00:00"},
        ])
        cache.set_inbox(acc_id, [
            {"id": "1", "from": "a@b.com", "to": "x@icloud.com", "subject": "Hello Duplicate", "date": "2025-01-03T00:00:00"},
            {"id": "3", "from": "e@f.com", "to": "z@icloud.com", "subject": "New", "date": "2025-01-04T00:00:00"},
        ])
        cached = cache.get_inbox(acc_id)
        
        # 增量写入时，已缓存的 id 不应被重复追加
        assert len(cached) == 3, f"期望 3 封，实际 {len(cached)}"
        assert [email["id"] for email in cached] == ["1", "2", "3"]
        assert cached[0]["subject"] == "Hello"
    finally:
        cache.clear_account(acc_id)
    assert len(cache.get_inbox(acc_id)) == 0
    print("  PASS test_mail_cache_basic")


def test_strip_html():
    """HTML 标签剥离"""
    from icloud_mail import _strip_html
    
    html = "<html><body><p>Hello</p><br><div>World</div></body></html>"
    text = _strip_html(html)
    assert "Hello" in text
    assert "World" in text
    assert "<p>" not in text
    assert "<html>" not in text
    print("  PASS test_strip_html")


def test_strip_html_with_link():
    """HTML 链接保留文字"""
    from icloud_mail import _strip_html
    
    html = '<a href="https://example.com">Click here</a>'
    text = _strip_html(html)
    assert "Click here" in text
    assert "example.com" in text
    print("  PASS test_strip_html_with_link")


def test_icloud_hme_account_info():
    """ICloudHME 客户端有 get_account_info 方法"""
    from icloud_hme import ICloudHME
    client = ICloudHME({}, verbose=False)
    assert hasattr(client, "get_account_info")
    # 未校验前应返回 None
    assert client.get_account_info() is None
    print("  PASS test_icloud_hme_account_info")



def test_icloud_hme_build_url_adds_required_query_params():
    """HME API URL 必须带 Apple Web 必需的查询参数"""
    from urllib.parse import parse_qs, urlparse
    from icloud_hme import CLIENT_BUILD_NUMBER, ICloudHME

    client = ICloudHME({"X-APPLE-WEBAUTH-USER": "123456%3Aignored"}, verbose=False)
    client._client_id = "00000000-1111-4222-8333-444444444444"

    built = client._build_url("https://p01-maildomainws.icloud.com/v1/hme/list?existing=keep")
    query = parse_qs(urlparse(built).query)

    assert query["existing"] == ["keep"]
    assert query["clientBuildNumber"] == [CLIENT_BUILD_NUMBER]
    assert query["clientMasteringNumber"] == [CLIENT_BUILD_NUMBER]
    assert query["clientId"] == ["00000000-1111-4222-8333-444444444444"]
    assert query["dsid"] == ["123456"]
    print("  PASS test_icloud_hme_build_url_adds_required_query_params")


def test_icloud_hme_normalize_alias_preserves_apple_fields():
    """Apple HME 字段名在规范化输出中保持兼容"""
    from icloud_hme import ICloudHME

    alias = ICloudHME._normalize_alias({
        "hme": "Alias@Privaterelay.AppleID.com",
        "label": "Login alias",
        "note": "Created for regression coverage",
        "isActive": False,
        "createTimestamp": 1712345678000,
        "anonymousId": "anon-123",
        "forwardToEmail": "real@example.com",
        "origin": "https://example.com",
    })

    assert alias["hme"] == "alias@privaterelay.appleid.com"
    assert alias["label"] == "Login alias"
    assert alias["note"] == "Created for regression coverage"
    assert alias["isActive"] is False
    assert alias["createTimestamp"] == 1712345678000
    assert alias["anonymousId"] == "anon-123"
    assert alias["forwardToEmail"] == "real@example.com"
    assert alias["origin"] == "https://example.com"
    print("  PASS test_icloud_hme_normalize_alias_preserves_apple_fields")


def test_api_key_store_create_verify_and_revoke_isolated():
    """API Key 明文只在创建时返回，并按 active 状态鉴权"""
    import tempfile
    from api_keys import APIKeyStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = APIKeyStore(Path(tmpdir) / "api_keys.json")
        created = store.create("regression")
        raw_key = created.get("api_key", "")

        assert raw_key.startswith("hme_")
        verified = store.verify(raw_key)
        assert verified is not None
        assert verified["id"] == created["id"]
        assert store.verify(raw_key + "wrong") is None

        assert store.deactivate(created["id"]) is True
        assert store.verify(raw_key) is None
    print("  PASS test_api_key_store_create_verify_and_revoke_isolated")


def test_extract_verification_code_multilingual_and_rejects_alpha_noise():
    """验证码提取支持中英文正文，并拒绝纯字母噪声"""
    from icloud_mail import ICloudMail

    cases = [
        ("中文数字验证码", "您的验证码是 482913，请在 10 分钟内完成验证。", "482913"),
        ("英文混合验证码", "Use verification code A1B2C3 to finish signing in.", "A1B2C3"),
        ("纯字母噪声", "Your verification code is ABCDEF", ""),
    ]

    for name, body, expected in cases:
        actual = ICloudMail.extract_verification_code(body)
        assert actual == expected, f"{name}: expected {expected!r}, got {actual!r}"
    print("  PASS test_extract_verification_code_multilingual_and_rejects_alpha_noise")

if __name__ == "__main__":
    tests = [
        ("parse_cookie_header_string", test_parse_cookie_header_string),
        ("parse_cookie_json", test_parse_cookie_json),
        ("parse_empty_input", test_parse_empty_input),
        ("derive_icloud_email_primary", test_derive_icloud_email_primary),
        ("derive_icloud_email_appleid_is_icloud", test_derive_icloud_email_appleid_is_icloud),
        ("derive_icloud_email_third_party", test_derive_icloud_email_third_party),
        ("mail_cache_basic", test_mail_cache_basic),
        ("strip_html", test_strip_html),
        ("strip_html_with_link", test_strip_html_with_link),
        ("icloud_hme_account_info", test_icloud_hme_account_info),
        ("icloud_hme_build_url_adds_required_query_params", test_icloud_hme_build_url_adds_required_query_params),
        ("icloud_hme_normalize_alias_preserves_apple_fields", test_icloud_hme_normalize_alias_preserves_apple_fields),
        ("api_key_store_create_verify_and_revoke_isolated", test_api_key_store_create_verify_and_revoke_isolated),
        ("extract_verification_code_multilingual_and_rejects_alpha_noise", test_extract_verification_code_multilingual_and_rejects_alpha_noise),
    ]
    
    passed = 0
    failed = 0
    
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            failed += 1
    
    print(f"\n{'='*40}")
    print(f"结果: {passed} 通过, {failed} 失败")
    
    if failed:
        sys.exit(1)
