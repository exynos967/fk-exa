"""
Exa 协议注册机
使用 HTTP 请求 + 外置 Turnstile Solver 完成注册，浏览器仅作为回退。
"""
import os
import re
import threading
import time

import requests as std_requests

from config import EMAIL_CODE_TIMEOUT
from mail_provider import get_email_code
from turnstile_solver import solve_turnstile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "exa-keys.txt")
_SAVE_LOCK = threading.Lock()
_ACCOUNT_PASSWORD_LABEL = "EMAIL_OTP_ONLY"

_EXA_SITEKEY = "0x4AAAAAADSpJWQOnICEKAwx"
_EXA_PROMO_CODE = "EXA50API"

_AUTH_BASE = "https://auth.exa.ai"
_DASHBOARD_BASE = "https://dashboard.exa.ai"
_CALLBACK_URL = "https://dashboard.exa.ai/"

_EXA_AUTH_URL = f"{_AUTH_BASE}/?callbackUrl=https%3A%2F%2Fdashboard.exa.ai%2F"

_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


def save_account(email, api_key):
    """并发注册时串行写入 exa-keys.txt（一行一个 key）。"""
    with _SAVE_LOCK:
        with open(_SAVE_FILE, "a", encoding="utf-8") as file_obj:
            file_obj.write(f"{api_key}\n")


def verify_api_key(api_key, timeout=30):
    """真实调用 Exa API，验证新 key 可用。"""
    try:
        response = std_requests.post(
            "https://api.exa.ai/search",
            json={"query": "api key verification", "numResults": 1},
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


# ──────────────────────────────────────────────
# Dashboard API 操作（cookie-authenticated）
# ──────────────────────────────────────────────

def _dashboard_request(session, method, path, **kwargs):
    """对 dashboard.exa.ai 发请求，自动携带 session cookies。"""
    url = f"{_DASHBOARD_BASE}{path}"
    headers = dict(_SESSION_HEADERS)
    headers.update(kwargs.pop("headers", {}))
    return session.request(method, url, headers=headers, **kwargs)


def _complete_onboarding(session):
    """完成 onboarding 流程，使 hasCompletedNewAiOnboarding=true。"""
    # 先查 session 确认是否需要 onboarding
    try:
        resp = _dashboard_request(session, "GET", "/api/auth/session")
        if resp.status_code != 200:
            print("⚠️  获取 session 失败，跳过 onboarding 检查")
            return False

        session_data = resp.json()
        user = session_data.get("user", {})
        if not user.get("hasCompletedNewAiOnboarding", True):
            print("📋 检测到未完成 onboarding，尝试跳过...")
        else:
            print("✅ Onboarding 已完成")
            return True
    except Exception as exc:
        print(f"⚠️  检查 onboarding 状态失败: {exc}")
        # 即使查询失败也继续尝试
        pass

    # 尝试通过 onboarding API 完成
    try:
        # 常见模式：POST 到 onboarding 相关端点标记为完成
        resp = _dashboard_request(
            session,
            "POST",
            "/api/complete-onboarding",
            json={"hasCompletedNewAiOnboarding": True},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            print("✅ Onboarding 标记完成")
            return True

        # 尝试 PATCH user
        resp = _dashboard_request(
            session,
            "PATCH",
            "/api/auth/session",
            json={"hasCompletedNewAiOnboarding": True},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            print("✅ Onboarding 标记完成 (session patch)")
            return True

        # 尝试 PUT
        resp = _dashboard_request(
            session,
            "PUT",
            "/api/user/onboarding",
            json={"completed": True},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            print("✅ Onboarding 标记完成 (PUT)")
            return True
    except Exception as exc:
        print(f"⚠️  Onboarding API 调用失败: {exc}")

    # 如果 API 方式都失败，尝试直接访问 onboarding 并跳过
    print("⚠️  API onboarding 未成功，尝试浏览器回退方式...")
    return _complete_onboarding_browser(session)


def _complete_onboarding_browser(session):
    """使用浏览器完成 onboarding——提取 session cookie 后注入浏览器。"""
    try:
        from camoufox.sync_api import Camoufox
        from config import REGISTER_HEADLESS

        cookies = _extract_cookies_for_playwright(session)
        with Camoufox(headless=REGISTER_HEADLESS) as browser:
            page = browser.new_page()
            # 注入 cookies
            page.goto(_DASHBOARD_BASE, wait_until="domcontentloaded", timeout=15000)
            for cookie in cookies:
                try:
                    page.context.add_cookies([cookie])
                except Exception:
                    pass

            page.goto(f"{_DASHBOARD_BASE}/home", wait_until="networkidle", timeout=30000)

            # 如果在 /onboarding 页面，尝试点击 Skip
            if "/onboarding" in page.url.lower():
                # 尝试各种 skip 按钮
                for selector in [
                    'button:text-is("Skip")',
                    'button:text-is("Continue")',
                    'button:text-is("Get started")',
                    'button:text-is("Next")',
                ]:
                    try:
                        if page.query_selector(selector):
                            page.click(selector, no_wait_after=True)
                            time.sleep(2)
                    except Exception:
                        continue

                # 再试一轮
                for selector in [
                    'button:text-is("Skip")',
                    'button:text-is("Continue")',
                    'button:text-is("I\'m done")',
                ]:
                    try:
                        if page.query_selector(selector):
                            page.click(selector, no_wait_after=True)
                            time.sleep(1)
                    except Exception:
                        continue

            # 更新 session cookies
            _update_session_from_browser(session, page)
            return True
    except Exception as exc:
        print(f"⚠️  浏览器 onboarding 失败: {exc}")
        return False


def _redeem_promo_code(session, code=_EXA_PROMO_CODE):
    """兑换赠金兑换码。"""
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
            resp = _dashboard_request(
                session, method, path, json=body, timeout=15
            )
            if resp.status_code in (200, 201):
                print(f"✅ 兑换码 {code} 已兑换 (via {path})")
                return True
            if resp.status_code == 404:
                continue
            # 非 404 可能是已兑换或其他状态
            if resp.status_code in (400, 409, 422):
                print(f"    {path}: {resp.status_code} {resp.text[:100]}")
        except Exception:
            continue

    print(f"⚠️  兑换码 {code} 无法通过 API 兑换，跳过")
    return False


def _get_or_create_api_key(session):
    """获取已有 API Key，没有则新建。"""
    # 1. 尝试 GET 已有 key
    try:
        resp = _dashboard_request(session, "GET", "/api/get-api-keys", timeout=15)
        if resp.status_code == 200:
            keys = resp.json().get("apiKeys", [])
            if keys:
                # 返回第一个 enabled 的 key 的 id（就是完整 key）
                for key_data in keys:
                    if key_data.get("enabled", True):
                        api_key = key_data.get("id", "").strip()
                        if re.match(
                            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                            api_key,
                            re.I,
                        ):
                            print(f"✅ 获取到已有 API Key: {api_key[:20]}...")
                            return api_key
    except Exception as exc:
        print(f"⚠️  获取已有 API Key 失败: {exc}")

    # 2. 尝试创建新 key
    create_endpoints = [
        ("POST", "/api/create-api-key", {"name": "auto-generated"}),
        ("POST", "/api/api-keys", {"name": "auto-generated"}),
        ("POST", "/api/get-api-keys", {"name": "auto-generated", "action": "create"}),
    ]

    for method, path, body in create_endpoints:
        try:
            resp = _dashboard_request(
                session, method, path, json=body, timeout=15
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                api_key = data.get("id") or data.get("key") or data.get("apiKey") or ""
                if api_key:
                    print(f"✅ 创建 API Key 成功: {api_key[:20]}...")
                    return api_key
            if resp.status_code == 404:
                continue
        except Exception:
            continue

    print("❌ 无法获取或创建 API Key")
    return None


# ──────────────────────────────────────────────
# 认证流程
# ──────────────────────────────────────────────

def _create_session():
    """创建 requests Session 并获取初始 cookies（含 cf_clearance）。"""
    session = std_requests.Session()
    session.headers.update(_SESSION_HEADERS)
    # 访问 auth 页面获取 Cloudflare 初始 cookies
    try:
        session.get(_EXA_AUTH_URL, timeout=15)
    except Exception:
        pass
    return session


def _has_cf_clearance(session):
    """检查 session 是否已持有 cf_clearance cookie。"""
    return any(
        "cf_clearance" in (c.name or "")
        for c in session.cookies
    )


def _ensure_cf_clearance(session):
    """确保 session 持有 cf_clearance，没有则尝试通过 Solver 获取。"""
    if _has_cf_clearance(session):
        print("✅ cf_clearance 已就绪")
        return True

    print("⚠️  缺少 cf_clearance，尝试通过 Turnstile Solver 获取...")
    turnstile_token = solve_turnstile(_EXA_AUTH_URL, _EXA_SITEKEY)
    if not turnstile_token:
        print("❌ 无法获取 Turnstile token，cf_clearance 获取失败")
        return False

    # 用 token 访问一次 auth 页面让 CF 设置 clearance
    try:
        resp = session.get(
            _EXA_AUTH_URL,
            headers={"Cookie": f"cf-turnstile-response={turnstile_token}"},
            timeout=15,
        )
    except Exception:
        pass

    # 再检查一次
    if _has_cf_clearance(session):
        print("✅ cf_clearance 已获取")
        return True

    # 最后尝试：通过 signin/email 请求让服务器端验证并设置
    print("⚠️  cf_clearance 仍缺失，将在 signin 请求后自动获取")
    return True  # 不阻塞流程，signin 成功后 CF 会设置


def _get_csrf_token(session):
    """获取 NextAuth CSRF token。"""
    resp = session.get(
        f"{_AUTH_BASE}/api/auth/csrf",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["csrfToken"]


def _send_verification_email(session, email, csrf_token):
    """POST signin/email，触发验证码邮件。

    需要真实的 cf-turnstile-response token。
    """
    # 先调用外置 Solver 获取 Turnstile token
    print("🔐 获取 Turnstile token...")
    turnstile_token = solve_turnstile(_EXA_AUTH_URL, _EXA_SITEKEY)
    if not turnstile_token:
        print("❌ 无法获取 Turnstile token")
        return False

    resp = session.post(
        f"{_AUTH_BASE}/api/auth/signin/email",
        data={
            "email": email,
            "csrfToken": csrf_token,
            "callbackUrl": _CALLBACK_URL,
            "json": "true",
            "cf-turnstile-response": turnstile_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )

    # 检查响应
    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code == 403 or data.get("error"):
        error = data.get("error", resp.text)
        print(f"❌ 发送验证邮件失败: {error}")
        return False

    if resp.status_code in (200, 302):
        # 检查 signin 响应是否带回了 cf_clearance
        cf_cookies = [c for c in session.cookies if "cf" in c.name.lower()]
        if cf_cookies:
            print(f"✅ signin 响应包含 CF cookies: {[c.name for c in cf_cookies]}")
        return True

    # NextAuth email provider 的 signin 成功后通常返回 302 或 200 带 redirect url
    print(f"⚠️  signin/email 返回 {resp.status_code}: {resp.text[:200]}")
    return resp.status_code < 400


def _verify_email_code(session, email, code):
    """GET callback/email 验证验证码，返回是否成功。"""
    # callback 前确保有 cf_clearance，缺少则直接走浏览器回退
    if not _has_cf_clearance(session):
        print("⚠️  缺少 cf_clearance，callback 大概率失败，切换到浏览器回退")
        return False

    params = {
        "token": code,
        "email": email,
        "callbackUrl": _CALLBACK_URL,
    }

    # 不跟随重定向，手动处理
    resp = session.get(
        f"{_AUTH_BASE}/api/auth/callback/email",
        params=params,
        timeout=15,
        allow_redirects=False,
    )

    # 检查响应
    if resp.status_code in (302, 303):
        # 重定向到 dashboard，说明验证成功
        # 跟随重定向以获取 dashboard session
        location = resp.headers.get("Location", "")
        if _DASHBOARD_BASE in location or location.startswith("/"):
            # 跟随重定向到 dashboard 以完成 session 设置
            redirect_url = location if location.startswith("http") else f"{_DASHBOARD_BASE}{location}"
            try:
                resp2 = session.get(redirect_url, timeout=15)
            except Exception:
                pass
        return True

    # 检查是否是验证错误
    if "error=" in resp.headers.get("Location", "") or "error=" in (resp.text or ""):
        error_match = re.search(r"error=(\w+)", resp.headers.get("Location", "") + resp.text)
        error = error_match.group(1) if error_match else "unknown"
        print(f"❌ 验证码验证失败: {error}")
        return False

    if resp.status_code == 200:
        # 可能已经设置了 session cookie
        return True

    print(f"⚠️  callback/email 返回 {resp.status_code}")
    return resp.status_code < 400


def _verify_email_code_browser(email, code):
    """使用浏览器回退方式验证验证码。"""
    print("🔧 回退到浏览器模式验证验证码...")
    try:
        from camoufox.sync_api import Camoufox
        from config import REGISTER_HEADLESS

        with Camoufox(headless=REGISTER_HEADLESS) as browser:
            page = browser.new_page()
            # 直接访问 callback URL
            callback_url = (
                f"{_AUTH_BASE}/api/auth/callback/email"
                f"?token={code}&email={email}&callbackUrl=https%3A%2F%2Fdashboard.exa.ai%2F"
            )
            page.goto(callback_url, wait_until="networkidle", timeout=30000)

            # 应该被重定向到 dashboard
            page.wait_for_url("**/dashboard.exa.ai/**", timeout=15000, wait_until="domcontentloaded")
            print("✅ 浏览器验证成功，提取 cookies...")

            return _extract_session_from_browser(page)
    except Exception as exc:
        print(f"❌ 浏览器验证失败: {exc}")
        return None


def _extract_session_from_browser(page):
    """从浏览器页面提取 session 并转为 requests Session。"""
    session = std_requests.Session()
    session.headers.update(_SESSION_HEADERS)

    try:
        all_cookies = page.context.cookies()
        cf_found = False
        for cookie in all_cookies:
            domain = cookie.get("domain", "")
            if "exa.ai" in domain or "exa.ai" in cookie.get("domain", ""):
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain", ""),
                    path=cookie.get("path", "/"),
                )
                if "cf_clearance" in (cookie.get("name") or "").lower():
                    cf_found = True
        if cf_found:
            print("✅ 已提取 cf_clearance cookie")
        else:
            print("⚠️  浏览器中未找到 cf_clearance cookie")
    except Exception as exc:
        print(f"⚠️  提取 cookies 失败: {exc}")

    return session


def _extract_cookies_for_playwright(session):
    """将 requests Session cookies 转为 Playwright 格式。"""
    cookies = []
    for cookie in session.cookies:
        cookies.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or ".exa.ai",
            "path": cookie.path or "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        })
    return cookies


def _update_session_from_browser(session, page):
    """从浏览器更新 requests Session 的 cookies。"""
    try:
        for cookie in page.context.cookies():
            domain = cookie.get("domain", "")
            if "exa.ai" in domain:
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=domain,
                    path=cookie.get("path", "/"),
                )
    except Exception:
        pass


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def register_with_api(email, password):
    """使用 HTTP 协议注册 Exa 账号。

    流程：
    1. 获取 CSRF token
    2. 解决 Turnstile
    3. 发送验证码到邮箱
    4. 用验证码完成认证
    5. 完成 onboarding
    6. 兑换赠金码 EXA50API
    7. 获取/创建 API Key
    8. 输出到 exa_accounts.txt
    """
    print(f"🌐 使用协议模式注册 Exa: {email}")

    # ── Phase 1: 认证 ──
    session = _create_session()

    try:
        csrf_token = _get_csrf_token(session)
        print(f"✅ CSRF token 已获取")
    except Exception as exc:
        print(f"❌ 获取 CSRF token 失败: {exc}")
        return None

    # 确保持有 cf_clearance（通过 Solver 获取 Turnstile token 并换取）
    _ensure_cf_clearance(session)

    if not _send_verification_email(session, email, csrf_token):
        return None
    print("✅ 验证邮件已发送")

    # 等待邮箱验证码
    code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT, service="exa")
    if not code:
        return None

    # 先尝试纯 HTTP 验证
    verified = _verify_email_code(session, email, code)
    if not verified:
        # 回退到浏览器
        session = _verify_email_code_browser(email, code)
        if not session:
            return None
    else:
        print("✅ 邮箱验证码验证成功")

    # 确认 session 有效
    try:
        resp = _dashboard_request(session, "GET", "/api/auth/session")
        if resp.status_code != 200:
            print("⚠️  Dashboard session 无效，尝试浏览器回退...")
            session = _verify_email_code_browser(email, code)
            if not session:
                return None
        else:
            user_email = resp.json().get("user", {}).get("email", "")
            print(f"✅ Dashboard 已登录: {user_email}")
    except Exception:
        pass

    # ── Phase 2: Onboarding ──
    _complete_onboarding(session)

    # ── Phase 3: 兑换赠金 ──
    _redeem_promo_code(session, _EXA_PROMO_CODE)

    # ── Phase 4: 获取 API Key ──
    api_key = _get_or_create_api_key(session)
    if not api_key:
        print("⚠️  无法获取 API Key")
        return "SUCCESS_NO_KEY"

    # ── Phase 5: 验证 & 保存 ──
    print("🧪 验证 API Key 可用性...")
    if not verify_api_key(api_key):
        # 即使验证失败也保存（可能是临时网络问题）
        print("⚠️  API Key 验证失败，但依然保存")

    save_account(email, api_key)

    print("🎉 Exa 注册成功")
    print(f"   邮箱: {email}")
    print(f"   密码: {_ACCOUNT_PASSWORD_LABEL}")
    print(f"   Key : {api_key}")
    return api_key


if __name__ == "__main__":
    from mail_provider import create_email

    email, password = create_email(service="exa")
    result = register_with_api(email, password)
    if result:
        print(f"✅ 注册成功: {email}")
    else:
        print(f"❌ 注册失败: {email}")
