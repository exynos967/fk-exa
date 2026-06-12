"""
使用 Camoufox 完成 Exa 注册
思路：通过邮箱验证码登录，跳过 onboarding，并提取默认 API Key。
若页面出现 Turnstile 挑战，通过外置 Solver 解决。
"""
import json
import os
import re
import threading
import time

import requests as std_requests
from camoufox.sync_api import Camoufox

from config import API_KEY_TIMEOUT, EMAIL_CODE_TIMEOUT, REGISTER_HEADLESS
from mail_provider import get_email_code
from turnstile_solver import solve_turnstile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "exa_accounts.txt")
_SAVE_LOCK = threading.Lock()
_ACCOUNT_PASSWORD_LABEL = "EMAIL_OTP_ONLY"
_EXA_AUTH_URL = "https://auth.exa.ai/?callbackUrl=https%3A%2F%2Fdashboard.exa.ai%2F"
_EXA_HOME_URL = "https://dashboard.exa.ai/home"
_EXA_TURNSTILE_SITEKEY = "0x4AAAAAADSpJWQOnICEKAwx"


def fill_first_input(page, selectors, value):
    """填充第一个存在的输入框"""
    for selector in selectors:
        if page.query_selector(selector):
            page.fill(selector, value)
            return selector
    return None


def click_first(page, selectors):
    """点击第一个存在的按钮/链接"""
    for selector in selectors:
        if page.query_selector(selector):
            page.click(selector, no_wait_after=True)
            return True
    return False


def extract_api_key(page):
    """从页面文本或 HTML 中提取 Exa API Key。"""
    patterns = []

    try:
        patterns.extend(re.findall(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", page.locator("main").inner_text(), re.I))
    except Exception:
        pass

    try:
        patterns.extend(re.findall(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", page.content(), re.I))
    except Exception:
        pass

    for candidate in patterns:
        return candidate
    return None


def fetch_api_key_via_dashboard_api(page):
    """直接调用已登录 dashboard 的 get-api-keys 接口，优先拿完整 key。"""
    try:
        payload = page.evaluate(
            """
            async () => {
                const response = await fetch('/api/get-api-keys', {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                        'accept': 'application/json',
                    },
                });

                return {
                    status: response.status,
                    body: await response.text(),
                };
            }
            """
        )
    except Exception:
        return None

    if int(payload.get("status") or 0) != 200:
        return None

    try:
        data = json.loads(payload.get("body") or "{}")
    except Exception:
        return None

    for item in data.get("apiKeys", []):
        candidate = (item.get("id") or "").strip()
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", candidate, re.I):
            return candidate
    return None


def ensure_dashboard_ready(page):
    """跳过 onboarding，落到可读 API Key 的 dashboard 页面。"""
    if "dashboard.exa.ai" not in page.url.lower():
        page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")

    if "/onboarding" in page.url.lower():
        click_first(page, ['button:text-is("Skip")'])
        time.sleep(1)
        click_first(
            page,
            [
                'button:text-is("Yes, I don\\\'t want the $10 in credits anyway!")',
                'button:text-is("Yes")',
            ],
        )
        page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

    if "/home" not in page.url.lower():
        page.goto(_EXA_HOME_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)


def wait_for_api_key(page, timeout=20):
    """等待主页 API Key 卡片渲染并显示完整 key。"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        ensure_dashboard_ready(page)
        api_key = fetch_api_key_via_dashboard_api(page)
        if api_key:
            return api_key

        click_first(page, ['button:text-is("Show")'])
        time.sleep(1)
        api_key = extract_api_key(page)
        if api_key:
            return api_key
        time.sleep(1)
    return None


def save_account(email, api_key):
    """并发注册时串行写入 exa_accounts.txt"""
    with _SAVE_LOCK:
        with open(_SAVE_FILE, "a", encoding="utf-8") as file_obj:
            file_obj.write(f"{email},{_ACCOUNT_PASSWORD_LABEL},{api_key}\n")


def verify_api_key(api_key, timeout=30):
    """真实调用 Exa API，验证新 key 可用"""
    try:
        response = std_requests.post(
            "https://api.exa.ai/search",
            json={
                "query": "api key verification",
                "numResults": 1,
            },
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
    except Exception as exc:
        print(f"❌ API Key 调用测试失败: {exc}")
        return False

    if response.status_code == 200:
        print("✅ API Key 调用测试通过")
        return True

    preview = response.text.strip().replace("\n", " ")[:160]
    print(f"❌ API Key 调用测试失败: HTTP {response.status_code}")
    if preview:
        print(f"   响应: {preview}")
    return False


def _detect_and_solve_turnstile(page):
    """检测页面上是否有 Turnstile 挑战，如有则通过外置 Solver 解决并注入 token。"""
    try:
        has_widget = page.locator('.cf-turnstile, [data-sitekey]').count()
        has_iframe = page.locator('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]').count()
    except Exception:
        return

    if not has_widget and not has_iframe:
        return

    print("🔐 检测到 Turnstile 挑战，调用外置 Solver...")
    token = solve_turnstile(page.url, _EXA_TURNSTILE_SITEKEY)
    if not token:
        print("⚠️  Turnstile 解决失败，继续尝试注册...")
        return

    # 注入 token 到页面
    page.evaluate(
        """
        (token) => {
            // 设置隐藏的 cf-turnstile-response 输入框
            let input = document.querySelector('input[name="cf-turnstile-response"]');
            if (!input) {
                input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'cf-turnstile-response';
                document.body.appendChild(input);
            }
            input.value = token;

            // 触发 Turnstile callback（如果存在）
            if (typeof window._turnstileTokenCallback === 'function') {
                window._turnstileTokenCallback(token);
            }
            if (typeof window.turnstileCallback === 'function') {
                window.turnstileCallback(token);
            }
        }
        """,
        token,
    )
    print("✅ Turnstile token 已注入")


def register_with_browser(email, password):
    """使用浏览器完成 Exa 邮箱验证码注册"""
    print(f"🌐 使用浏览器模式注册 Exa: {email}")

    try:
        with Camoufox(headless=REGISTER_HEADLESS) as browser:
            page = browser.new_page()

            page.goto(_EXA_AUTH_URL, wait_until="networkidle", timeout=30000)
            time.sleep(2)

            # 检测并解决 Turnstile 挑战
            _detect_and_solve_turnstile(page)

            email_selector = fill_first_input(
                page,
                ['input[type="email"]', 'input[placeholder="Email"]', 'input[aria-label="Email"]'],
                email,
            )
            if not email_selector:
                print("❌ Exa 登录页未找到邮箱输入框")
                return None

            if not click_first(page, ['button:text-is("Continue")']):
                print("❌ Exa 登录页未找到 Continue 按钮")
                return None

            page.wait_for_selector('input[placeholder*="verification" i], input[aria-label*="verification" i]', timeout=30000)
            print("✅ 到达 Exa 邮箱验证码页")

            code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT, service="exa")
            if not code:
                return None

            code_selector = fill_first_input(
                page,
                ['input[placeholder*="verification" i]', 'input[aria-label*="verification" i]'],
                code,
            )
            if not code_selector:
                print("❌ Exa 验证码页未找到输入框")
                return None

            if not click_first(page, ['button:text-is("VERIFY CODE")', 'button:text-is("Verify Code")', 'button:text-is("Verify")']):
                page.press(code_selector, "Enter")

            page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
            print("✅ Exa 登录成功")

            api_key = wait_for_api_key(page, timeout=API_KEY_TIMEOUT)
            if not api_key:
                print("⚠️  未找到 Exa API Key")
                return None

            print("🧪 验证 API Key 可用性...")
            if not verify_api_key(api_key):
                return None

            save_account(email, api_key)

            print("🎉 Exa 注册成功")
            print(f"   邮箱: {email}")
            print(f"   密码: {_ACCOUNT_PASSWORD_LABEL}")
            print(f"   Key : {api_key}")
            return api_key
    except Exception as exc:
        print(f"❌ Exa 注册失败: {exc}")
        return None
