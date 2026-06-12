"""
Exa 协议注册机
架构：浏览器完成 Turnstile + auth，纯 requests 完成 dashboard 操作。
"""
import os
import re
import threading
import time

import requests as std_requests
from camoufox.sync_api import Camoufox

from config import EMAIL_CODE_TIMEOUT, REGISTER_HEADLESS
from mail_provider import get_email_code

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "exa-keys.txt")
_SAVE_LOCK = threading.Lock()

_EXA_SITEKEY = "0x4AAAAAADSpJWQOnICEKAwx"
_EXA_PROMO_CODE = "EXA50API"

_AUTH_URL = "https://auth.exa.ai/?callbackUrl=https%3A%2F%2Fdashboard.exa.ai%2F"
_AUTH_BASE = "https://auth.exa.ai"
_DASHBOARD_BASE = "https://dashboard.exa.ai"

_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ──────────────────────────────────────────────
# Dashboard API（纯 requests）
# ──────────────────────────────────────────────

def _extract_cookies_from_browser(page):
    """从 Camoufox 浏览器提取 session cookies 到 dict。"""
    cookies = {}
    try:
        for c in page.context.cookies():
            domain = c.get("domain", "")
            if "exa.ai" in domain:
                cookies[c["name"]] = c["value"]
    except Exception:
        pass
    return cookies


def _cookies_to_session(cookies):
    """将 cookie dict 转为 requests Session。"""
    session = std_requests.Session()
    session.headers.update(_SESSION_HEADERS)
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".exa.ai", path="/")
    return session


def _dashboard_request(session, method, path, **kwargs):
    """对 dashboard.exa.ai 发请求。"""
    return session.request(
        method,
        f"{_DASHBOARD_BASE}{path}",
        headers=dict(_SESSION_HEADERS, **kwargs.pop("headers", {})),
        **kwargs,
    )


def _complete_onboarding(session):
    """完成 onboarding，领取默认 $20 赠金。"""
    endpoints = [
        ("POST", "/api/complete-onboarding", {"hasCompletedNewAiOnboarding": True}),
        ("PATCH", "/api/auth/session", {"hasCompletedNewAiOnboarding": True}),
        ("PUT", "/api/user/onboarding", {"completed": True}),
    ]

    for method, path, body in endpoints:
        try:
            resp = _dashboard_request(session, method, path, json=body, timeout=15)
            if resp.status_code in (200, 201):
                print("✅ Onboarding 完成（已领 $20 赠金）")
                return True
        except Exception:
            continue

    print("⚠️  Onboarding API 未成功，可能需手动处理")
    return False


def _redeem_promo_code(session, code=_EXA_PROMO_CODE):
    """兑换赠金码。"""
    print(f"🎁 尝试兑换赠金码: {code}")
    endpoints = [
        ("POST", "/api/redeem", {"code": code}),
        ("POST", "/api/billing/redeem", {"code": code}),
        ("POST", "/api/redeem-code", {"code": code}),
        ("POST", "/api/billing/promo", {"promoCode": code}),
        ("POST", "/api/billing/redeem-promo", {"code": code}),
    ]

    for method, path, body in endpoints:
        try:
            resp = _dashboard_request(session, method, path, json=body, timeout=15)
            if resp.status_code in (200, 201):
                print(f"✅ 兑换码 {code} 已兑换")
                return True
            if resp.status_code == 404:
                continue
            if resp.status_code in (400, 409, 422):
                print(f"    {path}: {resp.status_code} {resp.text[:80]}")
        except Exception:
            continue

    print(f"⚠️  无法兑换 {code}")
    return False


def _get_or_create_api_key(session):
    """获取已有 API Key。"""
    try:
        resp = _dashboard_request(session, "GET", "/api/get-api-keys", timeout=15)
        if resp.status_code == 200:
            keys = resp.json().get("apiKeys", [])
            for key_data in keys:
                if key_data.get("enabled", True):
                    api_key = (key_data.get("id") or "").strip()
                    if re.match(
                        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                        api_key,
                        re.I,
                    ):
                        print(f"✅ API Key: {api_key[:20]}...")
                        return api_key
    except Exception as exc:
        print(f"⚠️  获取 API Key 失败: {exc}")

    # 尝试创建
    for method, path, body in [
        ("POST", "/api/create-api-key", {"name": "auto-generated"}),
        ("POST", "/api/api-keys", {"name": "auto-generated"}),
    ]:
        try:
            resp = _dashboard_request(session, method, path, json=body, timeout=15)
            if resp.status_code in (200, 201):
                data = resp.json()
                key = data.get("id") or data.get("key") or data.get("apiKey") or ""
                if key and re.match(r"^[0-9a-f]{8}-", key, re.I):
                    print(f"✅ 创建 API Key: {key[:20]}...")
                    return key
        except Exception:
            continue

    print("❌ 无法获取或创建 API Key")
    return None


def verify_api_key(api_key, timeout=30):
    """验证 API Key 可用性。"""
    try:
        resp = std_requests.post(
            "https://api.exa.ai/search",
            json={"query": "api key verification", "numResults": 1},
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
    except Exception as exc:
        print(f"❌ API Key 调用测试失败: {exc}")
        return False

    if resp.status_code == 200:
        print("✅ API Key 调用测试通过")
        return True

    print(f"❌ API Key 验证失败: HTTP {resp.status_code}")
    return False


def save_account(api_key):
    """一行一个 key 写入 exa-keys.txt。"""
    with _SAVE_LOCK:
        with open(_SAVE_FILE, "a", encoding="utf-8") as f:
            f.write(f"{api_key}\n")


# ──────────────────────────────────────────────
# 浏览器 Auth 阶段
# ──────────────────────────────────────────────

def _fill_first_input(page, selectors, value):
    for selector in selectors:
        if page.query_selector(selector):
            page.fill(selector, value)
            return selector
    return None


def _click_first(page, selectors):
    for selector in selectors:
        if page.query_selector(selector):
            page.click(selector, no_wait_after=True)
            return True
    return False


def _wait_for_turnstile(page, timeout=15):
    """等待页面上的 Turnstile widget 自动解决。Camoufox 通常可以自动通过。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = page.evaluate("""
                (() => {
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    return input ? input.value : '';
                })()
            """)
            if token:
                print(f"✅ Turnstile 已自动解决")
                return token
        except Exception:
            pass

        # 检测 Turnstile checkbox 并尝试点击
        try:
            iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            if iframe:
                frame = iframe.content_frame()
                if frame:
                    checkbox = frame.query_selector('input[type="checkbox"], .cb-lb')
                    if checkbox:
                        checkbox.click(no_wait_after=True)
                        print("👆 点击 Turnstile checkbox...")
        except Exception:
            pass

        time.sleep(1)

    print("⚠️  Turnstile 未能在 {timeout}s 内自动解决")
    return None


def _browser_auth(email):
    """使用浏览器完成 Exa 认证（Turnstile + 邮箱验证码）。返回 cookies dict。"""
    print(f"🌐 浏览器认证 Exa: {email}")

    try:
        with Camoufox(headless=REGISTER_HEADLESS) as browser:
            page = browser.new_page()

            # 1. 访问 auth 页面
            page.goto(_AUTH_URL, wait_until="networkidle", timeout=30000)
            time.sleep(2)

            # 2. 等待 Turnstile 解决
            print("🔐 等待 Turnstile...")
            turnstile_token = _wait_for_turnstile(page, timeout=20)
            if not turnstile_token:
                print("⚠️  继续尝试注册（可能被拦截）...")

            # 3. 填写邮箱
            email_sel = _fill_first_input(
                page,
                ['input[type="email"]', 'input[placeholder="Email"]'],
                email,
            )
            if not email_sel:
                print("❌ 未找到邮箱输入框")
                return None

            # 4. 点击 Continue
            if not _click_first(page, ['button:text-is("Continue")']):
                print("❌ 未找到 Continue 按钮")
                return None

            # 5. 等待验证码输入页
            try:
                page.wait_for_selector(
                    'input[placeholder*="verification" i], '
                    'input[aria-label*="verification" i], '
                    'input[placeholder*="code" i]',
                    timeout=30000,
                )
                print("✅ 到达验证码页")
            except Exception:
                print("❌ 未到达验证码页，可能被风控拦截")
                return None

            # 6. 获取验证码
            code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT, service="exa")
            if not code:
                return None

            # 7. 填写验证码
            code_sel = _fill_first_input(
                page,
                [
                    'input[placeholder*="verification" i]',
                    'input[placeholder*="code" i]',
                    'input[aria-label*="verification" i]',
                ],
                code,
            )
            if not code_sel:
                print("❌ 未找到验证码输入框")
                return None

            # 8. 提交验证码
            if not _click_first(page, [
                'button:text-is("VERIFY CODE")',
                'button:text-is("Verify Code")',
                'button:text-is("Verify")',
            ]):
                page.press(code_sel, "Enter")

            # 9. 等待跳转到 dashboard
            page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
            print("✅ Exa 登录成功")

            # 10. 提取 cookies
            cookies = _extract_cookies_from_browser(page)
            session_cookie = cookies.get("next-auth.session-token", "")
            cf_clearance = cookies.get("cf_clearance", "")
            print(f"✅ session-token: {'有' if session_cookie else '无'}")
            print(f"✅ cf_clearance: {'有' if cf_clearance else '无'}")

            return cookies

    except Exception as exc:
        print(f"❌ 浏览器认证失败: {exc}")
        return None


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def register_with_api(email, password):
    """Exa 协议注册：浏览器 auth + requests dashboard。

    流程：
    1. 浏览器：Turnstile → 填邮箱 → 验证码 → 登录 → 提取 cookies
    2. requests：onboarding → 兑换 EXA50API → 获取 API Key
    3. 输出 exa-keys.txt（一行一个 Key）
    """
    print(f"🌐 Exa 注册: {email}")

    # ── Phase 1: 浏览器 auth ──
    cookies = _browser_auth(email)
    if not cookies:
        return None

    session = _cookies_to_session(cookies)

    # ── Phase 2: Dashboard 操作（纯 requests）──
    _complete_onboarding(session)
    _redeem_promo_code(session)

    api_key = _get_or_create_api_key(session)
    if not api_key:
        print("⚠️  无法获取 API Key")
        return "SUCCESS_NO_KEY"

    # ── Phase 3: 验证 & 保存 ──
    print("🧪 验证 API Key...")
    verify_api_key(api_key)

    save_account(api_key)

    print(f"🎉 Exa 注册成功")
    print(f"   Key : {api_key}")
    return api_key


if __name__ == "__main__":
    from mail_provider import create_email

    e, p = create_email(service="exa")
    register_with_api(e, p)
