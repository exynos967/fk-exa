"""
Exa 协议注册机
架构：Chromium + Xvfb 完成 Turnstile + auth，requests 完成 dashboard。
"""
import os
import re
import threading
import time

import requests as std_requests
from playwright.sync_api import sync_playwright

from config import EMAIL_CODE_TIMEOUT
from mail_provider import get_email_code

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "exa-keys.txt")
_SAVE_LOCK = threading.Lock()

_EXA_PROMO_CODE = "EXA50API"
_AUTH_URL = "https://auth.exa.ai/?callbackUrl=https%3A%2F%2Fdashboard.exa.ai%2F"
_DASHBOARD_BASE = "https://dashboard.exa.ai"
_ONBOARDING_URL = "https://dashboard.exa.ai/onboarding?redirect=%2F"
_BILLING_URL = "https://dashboard.exa.ai/billing"
_API_KEYS_URL = "https://dashboard.exa.ai/api-keys"

_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


# ──────────────────────────────────────────────
# Dashboard API（纯 requests，需要 cookies）
# ──────────────────────────────────────────────

def _cookies_to_session(cookies):
    session = std_requests.Session()
    session.headers.update(_SESSION_HEADERS)
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".exa.ai", path="/")
    return session


def _dash_req(session, method, path, **kw):
    return session.request(method, f"{_DASHBOARD_BASE}{path}", timeout=kw.pop("timeout", 15), **kw)


def _complete_onboarding(session):
    """在浏览器里完成 onboarding。用 requests 尝试 API，失败则返回 False。"""
    for m, p, b in [
        ("POST", "/api/complete-onboarding", {"hasCompletedNewAiOnboarding": True}),
        ("PATCH", "/api/auth/session", {"hasCompletedNewAiOnboarding": True}),
    ]:
        try:
            r = _dash_req(session, m, p, json=b, timeout=15)
            if r.status_code in (200, 201):
                print("✅ Onboarding 完成")
                return True
        except Exception:
            continue
    return False


def _redeem_promo_code(session, code=_EXA_PROMO_CODE):
    print(f"🎁 兑换: {code}")
    for m, p, b in [
        ("POST", "/api/redeem", {"code": code}),
        ("POST", "/api/billing/redeem", {"code": code}),
        ("POST", "/api/redeem-code", {"code": code}),
    ]:
        try:
            r = _dash_req(session, m, p, json=b, timeout=15)
            if r.status_code in (200, 201):
                print(f"✅ {code} 已兑换")
                return True
        except Exception:
            continue
    print(f"⚠️  兑换 {code} 失败")
    return False


def _get_api_key(session):
    try:
        r = _dash_req(session, "GET", "/api/get-api-keys", timeout=15)
        if r.status_code == 200:
            for k in r.json().get("apiKeys", []):
                if k.get("enabled", True):
                    key = (k.get("id") or "").strip()
                    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", key, re.I):
                        print(f"✅ Key: {key[:20]}...")
                        return key
    except Exception:
        pass
    return None


def verify_api_key(api_key, timeout=30):
    try:
        r = std_requests.post(
            "https://api.exa.ai/search",
            json={"query": "test", "numResults": 1},
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
        print(f"🧪 验证: HTTP {r.status_code} {'✅' if r.status_code == 200 else '❌'}")
        return r.status_code == 200
    except Exception:
        return False


def save_account(api_key):
    with _SAVE_LOCK:
        with open(_SAVE_FILE, "a", encoding="utf-8") as f:
            f.write(f"{api_key}\n")


# ──────────────────────────────────────────────
# Camoufox 浏览器完整注册
# ──────────────────────────────────────────────

def _browser_full_flow(email):
    """Chromium + Xvfb 完成 Exa 完整注册流程"""
    print(f"🌐 Chromium 注册 Exa: {email}")

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>false})")

        # ========== Auth 阶段 ==========
        print("📡 访问 auth 页面...")
        page.goto(_AUTH_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        ts_info = page.evaluate("""JSON.stringify({
            widget: document.querySelectorAll('.cf-turnstile,[data-sitekey]').length,
            iframe: document.querySelectorAll('iframe[src*="challenges"]').length,
            api: typeof window.turnstile !== 'undefined'
        })""")
        print(f"   Turnstile: {ts_info}")

        email_el = page.query_selector('input[type="email"]')
        if not email_el:
            print("❌ 无邮箱输入框")
            return None
        email_el.fill(email)
        time.sleep(0.5)

        btn = page.query_selector('button:text-is("Continue")')
        if not btn:
            print("❌ 无 Continue 按钮")
            return None
        btn.click(no_wait_after=True)
        print("   ✅ Continue 已点击")
        time.sleep(5)

        ts_info2 = page.evaluate("""JSON.stringify({
            widget: document.querySelectorAll('.cf-turnstile,[data-sitekey]').length,
            iframe: document.querySelectorAll('iframe').length,
            body: document.body?.innerText?.substring(0, 200)
        })""")
        print(f"   点击后: {ts_info2}")

        # 尝试点击 Turnstile checkbox 触发自动解决
        for _ in range(8):
            try:
                iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
                if iframe:
                    frame = iframe.content_frame()
                    if frame:
                        cb = frame.query_selector('input[type="checkbox"], .cb-lb')
                        if cb:
                            cb.click(no_wait_after=True)
                            print("   👆 点击 Turnstile checkbox")
                            time.sleep(2)
            except Exception:
                pass
            time.sleep(1)

        try:
            page.wait_for_selector(
                'input[placeholder*="verification" i], input[aria-label*="verification" i]',
                timeout=30000,
            )
            print("✅ 到达验证码页")
        except Exception:
            body = page.evaluate("document.body?.innerText?.substring(0, 300) || ''")
            print(f"   ❌ 未到验证码页: {body[:200]}")
            return None

        code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT, service="exa")
        if not code:
            return None

        inputs = page.query_selector_all(
            'input[placeholder*="verification" i], input[aria-label*="verification" i]'
        )
        if inputs:
            inputs[0].fill(code)
            time.sleep(0.5)

        verify_btns = page.query_selector_all(
            'button:text-is("VERIFY CODE"), button:text-is("Verify Code"), button:text-is("Verify")'
        )
        if verify_btns:
            verify_btns[0].click(no_wait_after=True)
        else:
            page.keyboard.press("Enter")

        try:
            page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
            print("✅ Dashboard")
        except Exception:
            print(f"   ⚠️  未跳转 Dashboard: {page.url[:80]}")
            if "dashboard" not in page.url.lower():
                return None
        time.sleep(2)

        # ========== Dashboard 阶段 ==========

        if "/onboarding" in page.url.lower():
            print("📋 跳过 Onboarding...")
            for _ in range(3):
                for s in ['button:text-is("Skip")', 'button:text-is("Continue")',
                          'button:text-is("Next")', 'button:text-is("Get started")']:
                    try:
                        el = page.query_selector(s)
                        if el:
                            el.click(no_wait_after=True)
                            time.sleep(1.5)
                    except Exception:
                        pass

        print(f"🎁 兑换 {_EXA_PROMO_CODE}...")
        try:
            page.goto(_BILLING_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
            code_inputs = page.query_selector_all(
                'input[placeholder*="code" i], input[name*="code" i], '
                'input[placeholder*="promo" i], input[name*="promo" i]'
            )
            for ci in code_inputs:
                try:
                    ci.fill(_EXA_PROMO_CODE)
                    time.sleep(0.5)
                    page.keyboard.press("Enter")
                    time.sleep(2)
                except Exception:
                    pass
            print("   ✅ 兑换码已提交")
        except Exception as exc:
            print(f"   ⚠️  兑换码提交失败: {exc}")

        print("🔑 获取 API Key...")
        page.goto(_API_KEYS_URL, wait_until="domcontentloaded", timeout=15000)
        time.sleep(3)

        api_key = None
        try:
            html = page.content()
            matches = re.findall(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                html, re.I
            )
            for m in matches:
                if m:
                    api_key = m
                    print(f"   ✅ 从页面提取 Key: {api_key[:20]}...")
                    break
        except Exception:
            pass

        if not api_key:
            try:
                payload = page.evaluate("""
                    async () => {
                        const r = await fetch('/api/get-api-keys', {credentials:'include'});
                        return {status: r.status, body: await r.text()};
                    }
                """)
                if payload.get("status") == 200:
                    import json as _json
                    data = _json.loads(payload["body"])
                    for k in data.get("apiKeys", []):
                        if k.get("enabled", True):
                            api_key = (k.get("id") or "").strip()
                            print(f"   ✅ API 获取 Key: {api_key[:20]}...")
                            break
            except Exception:
                pass

        if not api_key:
            print("❌ 无法获取 API Key")
            return "SUCCESS_NO_KEY"

        cookies = {}
        for c in ctx.cookies():
            if "exa.ai" in (c.get("domain") or ""):
                cookies[c["name"]] = c["value"]

        return {"api_key": api_key, "cookies": cookies}

    except Exception as exc:
        print(f"❌ 异常: {exc}")
        import traceback; traceback.print_exc()
        return None
    finally:
        browser.close()
        pw.stop()


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def register_with_api(email, password):
    print(f"🌐 Exa 注册: {email}")

    result = _browser_full_flow(email)
    if not result:
        return None

    api_key = result.get("api_key") if isinstance(result, dict) else result
    cookies = result.get("cookies", {}) if isinstance(result, dict) else {}

    if api_key == "SUCCESS_NO_KEY" or not api_key:
        return "SUCCESS_NO_KEY"

    # 用 cookies 补做 dashboard 操作（浏览器里可能没做完）
    if cookies:
        session = _cookies_to_session(cookies)
        _complete_onboarding(session)
        _redeem_promo_code(session)

    verify_api_key(api_key)
    save_account(api_key)

    print(f"🎉 成功! Key: {api_key}")
    return api_key


if __name__ == "__main__":
    from mail_provider import create_email
    e, p = create_email(service="exa")
    register_with_api(e, p)
