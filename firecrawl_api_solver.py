"""
Firecrawl 协议注册机
仅走 curl_cffi + Next Server Action + Supabase PKCE + 邮箱验证链接协议流。
"""
import base64
import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, quote, urljoin, urlparse

from config import EMAIL_CODE_TIMEOUT
from mail_provider import get_verification_link

try:
    from curl_cffi import requests as http_requests
except Exception as exc:  # pragma: no cover - 启动期依赖错误
    raise RuntimeError("Firecrawl API-only 模式需要安装 curl_cffi") from exc

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "firecrawl-keys.txt")
_SAVE_LOCK = threading.Lock()

_FIRECRAWL_BASE = "https://www.firecrawl.dev"
_SIGNUP_URL = f"{_FIRECRAWL_BASE}/signin?view=signup&source=agent-suggested"
_ONBOARDING_REFERER = (
    f"{_FIRECRAWL_BASE}/onboarding?"
    "creditOffers=github,discord,twitter,linkedin,youtube,community"
    "&heardAbout=ai_search&step=use_case&hasViewedTerms=true&termsAccepted=true"
)
_MCL_URL = "https://mcl.spur.us/d/mcl.js?tk={token}"
_ROUTER_STATE_TREE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22signin%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%2C0%5D%7D%2Cnull%2Cnull%2C0%5D%7D%2Cnull%2Cnull%2C16%5D"
)
_ONBOARDING_ROUTER_STATE_TREE = quote(
    '["",{"children":["onboarding",{"children":["__PAGE__",{},null,null,0]},null,null,0]},null,null,16]',
    safe="",
)
_SUPABASE_URL = "https://alttmdsdujxrfnakrkyi.supabase.co"
_SUPABASE_KEY = "sb_publishable_1CcTB4SxsdcfOIjABzs4HA_hS7GdDDC"
_SUPABASE_STORAGE_KEY = "sb-alttmdsdujxrfnakrkyi-auth-token"
_SUPABASE_CODE_VERIFIER_COOKIE = f"{_SUPABASE_STORAGE_KEY}-code-verifier"
_COOKIE_CHUNK_SIZE = 3180
_BONUS_CREDITS = 400
_BONUS_CREDIT_OFFERS = ("github", "discord", "twitter", "linkedin", "youtube", "community")
_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_KEY_RE = re.compile(r"fc-[a-zA-Z0-9_-]{20,}")
_SERVER_ACTION_RE = re.compile(
    r'createServerReference\)\("([0-9a-f]{32,64})",[^)]*?,"([^"]+)"\)'
)
_ONBOARDING_ACTION_NAMES = (
    "checkOnboardingStatus",
    "completeOnboarding",
    "saveOnboardingData",
    "awardBonusCredits",
)


class FirecrawlProtocolError(RuntimeError):
    """Firecrawl 协议字段缺失或响应不符合预期。"""


def _new_http_session():
    session = http_requests.Session(impersonate="chrome131")
    session.headers.update(_SESSION_HEADERS)
    return session


def _http_request(method, url, **kwargs):
    kwargs.setdefault("impersonate", "chrome131")
    return http_requests.request(method, url, **kwargs)


def _fetch_signup_page(session):
    response = session.get(_SIGNUP_URL, timeout=30)
    response.raise_for_status()
    return response.text


def _extract_deployment_id(html):
    match = re.search(r'data-dpl-id="([^"]+)"', html)
    if match:
        return match.group(1)

    match = re.search(r"NEXT_DEPLOYMENT_ID['\"]?\s*[:=]\s*['\"]([^'\"]+)", html)
    if match:
        return match.group(1)

    raise FirecrawlProtocolError("注册页未找到 Next deployment id")


def _iter_script_urls(html):
    seen = set()
    for src in re.findall(r'<script[^>]+src="([^"]+)"', html):
        url = urljoin(_FIRECRAWL_BASE, src)
        if url in seen or "/_next/static/chunks/" not in url:
            continue
        seen.add(url)
        yield url


def _extract_server_action_id(js, expected_name):
    for match in _SERVER_ACTION_RE.finditer(js):
        action_id, action_name = match.groups()
        if action_name == expected_name:
            return action_id
    return None


def _extract_signup_action_id(js):
    return _extract_server_action_id(js, "signUp")


def _extract_mcl_token(js):
    patterns = (
        r'async function ez\(\)\{let e="([^"]+)"',
        r'mcl\.spur\.us/d/mcl\.js\?tk=\$\{e\}[^\n]+?let e="([^"]+)"',
        r'"(SMEGdTv[^"]{100,})"',
    )
    for pattern in patterns:
        match = re.search(pattern, js)
        if match:
            return match.group(1)
    return None


def _discover_signup_runtime(session, html):
    deployment_id = _extract_deployment_id(html)
    action_id = None
    mcl_token = None

    for script_url in _iter_script_urls(html):
        try:
            response = session.get(script_url, timeout=30)
            if response.status_code != 200:
                continue
            js = response.text
        except Exception:
            continue

        if not action_id:
            action_id = _extract_signup_action_id(js)
        if not mcl_token:
            mcl_token = _extract_mcl_token(js)
        if action_id and mcl_token:
            break

    if not action_id:
        raise FirecrawlProtocolError("未能从前端 chunk 解析 signUp Server Action id")
    if not mcl_token:
        raise FirecrawlProtocolError("未能从前端 chunk 解析 Monocle token")

    return {
        "deployment_id": deployment_id,
        "action_id": action_id,
        "mcl_token": mcl_token,
    }


def _get_monocle_assessment(session, mcl_token):
    response = session.get(
        _MCL_URL.format(token=mcl_token),
        allow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()

    match = re.search(r',M="([^"]+)"', response.text)
    if not match:
        raise FirecrawlProtocolError("Monocle 脚本未返回内置 assessment")

    assessment = match.group(1)
    if not assessment.startswith("eyJ"):
        raise FirecrawlProtocolError("Monocle assessment 格式异常")
    return assessment


def _extract_action_payload(response_text):
    """从 Next text/x-component 响应中提取 Server Action 返回对象。"""
    result_keys = (
        "data",
        "error",
        "message",
        "redirect",
        "requiresSmsVerification",
        "success",
        "creditsAwarded",
        "alreadyCompleted",
        "hasCompletedOnboarding",
    )
    for line in response_text.splitlines():
        if ":{" not in line:
            continue
        _, raw = line.split(":", 1)
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if any(key in payload for key in result_keys):
            return payload
    return None


def _json_b64url(value):
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return "base64-" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_json_b64url(value):
    if not value:
        return None
    encoded = value
    if encoded.startswith("base64-"):
        encoded = encoded[len("base64-"):]
    encoded += "=" * ((4 - len(encoded) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))


def _set_cookie(session, name, value, domain="www.firecrawl.dev"):
    session.cookies.set(name, value, domain=domain, path="/")


def _clear_chunked_cookie(session, name, domain="www.firecrawl.dev"):
    _set_cookie(session, name, "", domain=domain)
    for index in range(8):
        _set_cookie(session, f"{name}.{index}", "", domain=domain)


def _set_chunked_cookie(session, name, value, domain="www.firecrawl.dev"):
    _clear_chunked_cookie(session, name, domain=domain)
    if len(value) <= _COOKIE_CHUNK_SIZE:
        _set_cookie(session, name, value, domain=domain)
        return

    for index, offset in enumerate(range(0, len(value), _COOKIE_CHUNK_SIZE)):
        _set_cookie(
            session,
            f"{name}.{index}",
            value[offset:offset + _COOKIE_CHUNK_SIZE],
            domain=domain,
        )


def _extract_code_from_callback_url(callback_url):
    query = parse_qs(urlparse(callback_url).query)
    return (query.get("code") or [None])[0]


def _exchange_supabase_pkce_session(session, callback_url):
    auth_code = _extract_code_from_callback_url(callback_url)
    if not auth_code:
        raise FirecrawlProtocolError(f"邮箱回调未包含 Supabase auth code: {callback_url}")

    cookie_value = session.cookies.get(_SUPABASE_CODE_VERIFIER_COOKIE)
    code_verifier = _decode_json_b64url(cookie_value)
    if not code_verifier:
        raise FirecrawlProtocolError("缺少 Supabase PKCE code verifier cookie")

    response = session.post(
        f"{_SUPABASE_URL}/auth/v1/token?grant_type=pkce",
        json={"auth_code": auth_code, "code_verifier": code_verifier},
        headers={
            "apikey": _SUPABASE_KEY,
            "authorization": f"Bearer {_SUPABASE_KEY}",
            "content-type": "application/json",
            "x-client-info": "supabase-ssr/0.8.0 createBrowserClient",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    session_payload = {
        key: payload[key]
        for key in ("access_token", "token_type", "expires_in", "expires_at", "refresh_token", "user")
        if key in payload
    }
    if "access_token" not in session_payload or "user" not in session_payload:
        raise FirecrawlProtocolError("Supabase PKCE 交换未返回完整 session")

    _set_chunked_cookie(session, _SUPABASE_STORAGE_KEY, _json_b64url(session_payload))
    _clear_chunked_cookie(session, _SUPABASE_CODE_VERIFIER_COOKIE)
    return session_payload


def _signup(session, email, password, runtime):
    assessment = _get_monocle_assessment(session, runtime["mcl_token"])
    payload = [
        email.lower(),
        password,
        {
            "teamInvitationCode": None,
            "teamInvitationName": None,
            "redirect": None,
            "monocleAssessment": assessment,
        },
    ]
    headers = {
        "Accept": "text/x-component",
        "Content-Type": "text/plain;charset=UTF-8",
        "Next-Action": runtime["action_id"],
        "Next-Router-State-Tree": _ROUTER_STATE_TREE,
        "Origin": _FIRECRAWL_BASE,
        "Referer": _SIGNUP_URL,
        "X-Deployment-Id": runtime["deployment_id"],
    }

    response = session.post(
        _SIGNUP_URL,
        data=json.dumps(payload, separators=(",", ":")),
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()

    action_payload = _extract_action_payload(response.text)
    if not action_payload:
        preview = response.text.strip().replace("\n", " ")[:200]
        raise FirecrawlProtocolError(f"注册响应中未找到 action payload: {preview}")

    return action_payload


def _verify_email_link(session, verify_url):
    response = session.get(verify_url, allow_redirects=False, timeout=60)
    if response.status_code >= 400:
        print(f"❌ 邮箱验证链接访问失败: HTTP {response.status_code}")
        return None, None

    callback_url = response.headers.get("location") or str(response.url)
    if callback_url.startswith("/"):
        callback_url = urljoin(_FIRECRAWL_BASE, callback_url)

    if "auth_failed" in callback_url.lower():
        print(f"❌ 邮箱验证回调失败: {callback_url}")
        return None, None

    try:
        session_payload = _exchange_supabase_pkce_session(session, callback_url)
    except Exception as exc:
        print(f"❌ Supabase PKCE 登录态交换失败: {exc}")
        return None, None

    callback_response = session.get(callback_url, allow_redirects=True, timeout=60)
    final_url = str(callback_response.url)
    if "auth_failed" in final_url.lower():
        print(f"❌ 邮箱验证回调失败: {final_url}")
        return None, None

    print(f"✅ 邮箱验证完成并已建立登录态: {final_url}")
    return callback_response.text, session_payload


def _server_action(session, action_id, args, deployment_id, referer=_ONBOARDING_REFERER):
    response = session.post(
        referer,
        data=json.dumps(args, separators=(",", ":")),
        headers={
            "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8",
            "Next-Action": action_id,
            "Next-Router-State-Tree": _ONBOARDING_ROUTER_STATE_TREE,
            "Origin": _FIRECRAWL_BASE,
            "Referer": referer,
            "X-Deployment-Id": deployment_id,
        },
        timeout=60,
    )
    response.raise_for_status()
    return _extract_action_payload(response.text)


def _discover_onboarding_runtime(session):
    response = session.get(_ONBOARDING_REFERER, timeout=30)
    response.raise_for_status()
    html = response.text
    deployment_id = _extract_deployment_id(html)
    action_ids = {}

    for script_url in _iter_script_urls(html):
        try:
            script_response = session.get(script_url, timeout=30)
            if script_response.status_code != 200:
                continue
            js = script_response.text
        except Exception:
            continue

        for action_name in _ONBOARDING_ACTION_NAMES:
            if action_name not in action_ids:
                action_id = _extract_server_action_id(js, action_name)
                if action_id:
                    action_ids[action_name] = action_id
        if all(action_name in action_ids for action_name in _ONBOARDING_ACTION_NAMES):
            break

    missing = [name for name in _ONBOARDING_ACTION_NAMES if name not in action_ids]
    if missing:
        raise FirecrawlProtocolError(f"未能解析 onboarding Server Action: {', '.join(missing)}")

    return {"deployment_id": deployment_id, "action_ids": action_ids}


def _complete_onboarding(session, session_payload):
    print("🎁 完成 onboarding 并领取赠送点数...")
    runtime = _discover_onboarding_runtime(session)
    deployment_id = runtime["deployment_id"]
    actions = runtime["action_ids"]
    user = session_payload.get("user") if isinstance(session_payload, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None

    status = _server_action(session, actions["checkOnboardingStatus"], [], deployment_id)
    if isinstance(status, dict) and status.get("hasCompletedOnboarding"):
        print("ℹ️  onboarding 已完成，跳过重复提交")
        return True

    complete_result = _server_action(
        session,
        actions["completeOnboarding"],
        [
            {
                "origin": "",
                "customOrigin": "",
                "productUrl": "",
                "usage": "",
                "customUsage": "",
                "problemSolution": "",
            }
        ],
        deployment_id,
    )
    if isinstance(complete_result, dict) and complete_result.get("success") is False:
        print(f"⚠️  completeOnboarding 返回失败: {complete_result}")

    if user_id:
        _server_action(
            session,
            actions["saveOnboardingData"],
            [
                {
                    "userId": user_id,
                    "originSource": "ai_search",
                    "usagePurpose": "unknown",
                    "receive_product_updates": False,
                }
            ],
            deployment_id,
        )

    award_result = _server_action(
        session,
        actions["awardBonusCredits"],
        [{"credits": _BONUS_CREDITS}],
        deployment_id,
    )
    if isinstance(award_result, dict):
        if award_result.get("success") is False:
            print(f"⚠️  赠送点数领取返回失败: {award_result}")
        else:
            awarded = award_result.get("creditsAwarded")
            if awarded is not None:
                print(f"✅ 已领取赠送点数: {awarded}")
            else:
                print("✅ 赠送点数领取请求已提交")
    else:
        print(f"✅ 赠送点数领取请求已提交: {','.join(_BONUS_CREDIT_OFFERS)}")

    return True


def _extract_api_key_from_text(text):
    if not text:
        return None
    match = _KEY_RE.search(text)
    return match.group(0) if match else None


def _extract_api_key_from_payload(payload):
    if not isinstance(payload, dict):
        return None

    direct_values = [payload.get("apiKey"), payload.get("key"), payload.get("id")]
    for value in direct_values:
        if isinstance(value, str):
            key = _extract_api_key_from_text(value)
            if key:
                return key

    api_keys = payload.get("apiKeys")
    if isinstance(api_keys, list):
        for item in api_keys:
            if not isinstance(item, dict):
                continue
            if item.get("enabled") is False or item.get("isActive") is False:
                continue
            for field in ("apiKey", "key", "id", "token"):
                value = item.get(field)
                if isinstance(value, str):
                    key = _extract_api_key_from_text(value)
                    if key:
                        return key

    return _extract_api_key_from_text(json.dumps(payload, ensure_ascii=False))


def _get_api_key_from_user_team(session):
    try:
        response = session.get(
            f"{_FIRECRAWL_BASE}/api/user/team",
            headers={"Accept": "application/json", "Referer": f"{_FIRECRAWL_BASE}/app"},
            timeout=30,
        )
    except Exception as exc:
        print(f"⚠️  /api/user/team 请求失败: {exc}")
        return None

    if response.status_code != 200:
        preview = response.text.strip().replace("\n", " ")[:160]
        print(f"⚠️  /api/user/team 返回 HTTP {response.status_code}: {preview}")
        return None

    try:
        payload = response.json()
    except Exception:
        payload = None

    return _extract_api_key_from_payload(payload) if payload is not None else _extract_api_key_from_text(response.text)


def _get_api_key_from_pages(session):
    for path in (
        "/api/user/team",
        "/onboarding?creditOffers=github,discord,twitter,linkedin,youtube,community&heardAbout=ai_search&step=api_key&hasViewedTerms=true&termsAccepted=true",
        "/app",
        "/app/api-keys",
    ):
        try:
            response = session.get(
                f"{_FIRECRAWL_BASE}{path}",
                headers={"Accept": "application/json"} if path.startswith("/api/") else None,
                timeout=30,
            )
        except Exception:
            continue
        if response.status_code != 200:
            continue
        try:
            payload = response.json()
        except Exception:
            payload = None
        if payload is not None:
            key = _extract_api_key_from_payload(payload)
            if key:
                return key
        key = _extract_api_key_from_text(response.text)
        if key:
            return key
    return None


def _get_api_key(session, verification_page_html=None, retries=4, delay=2):
    key = _extract_api_key_from_text(verification_page_html or "")
    if key:
        print(f"✅ 验证回调页面已拿到 API Key: {key[:20]}...")
        return key

    for attempt in range(1, retries + 1):
        key = _get_api_key_from_user_team(session)
        if key:
            print(f"✅ /api/user/team 已拿到 API Key: {key[:20]}...")
            return key

        key = _get_api_key_from_pages(session)
        if key:
            print(f"✅ 页面中已拿到 API Key: {key[:20]}...")
            return key

        if attempt < retries:
            print(f"⏳ 等待 {delay} 秒后重试获取 API Key ({attempt}/{retries})...")
            time.sleep(delay)

    return None


def verify_api_key(api_key, timeout=30):
    try:
        response = _http_request(
            "POST",
            "https://api.firecrawl.dev/v2/scrape",
            json={"url": "https://example.com"},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except Exception as exc:
        print(f"⚠️  API Key 调用测试遇到网络/TLS 异常，暂时无法确认 Key 是否可用: {exc}")
        return None

    if response.status_code == 200:
        print("✅ API Key 调用测试通过")
        return True

    preview = response.text.strip().replace("\n", " ")[:160]
    print(f"❌ API Key 调用测试失败: HTTP {response.status_code}")
    if preview:
        print(f"   响应: {preview}")
    return False


def save_account(api_key):
    with _SAVE_LOCK:
        with open(_SAVE_FILE, "a", encoding="utf-8") as file:
            file.write(f"{api_key}\n")


def register_with_api(email, password):
    print(f"🌐 Firecrawl 协议注册: {email}")
    session = _new_http_session()

    try:
        print("🧭 初始化注册页...")
        html = _fetch_signup_page(session)
        runtime = _discover_signup_runtime(session, html)
        print("✅ 注册运行时参数已解析")

        print("📤 提交注册...")
        signup_result = _signup(session, email, password, runtime)
    except Exception as exc:
        print(f"❌ 协议注册初始化/提交失败: {exc}")
        return None

    if signup_result.get("requiresSmsVerification"):
        message = signup_result.get("message") or signup_result.get("error") or "Firecrawl 要求 SMS 验证"
        print(f"❌ {message}")
        return None

    if signup_result.get("error"):
        message = signup_result.get("message") or signup_result.get("error")
        print(f"❌ 注册失败: {message}")
        return None

    print("✅ 验证邮件已发送")
    redirect = signup_result.get("redirect")
    if redirect:
        print(f"   下一步: {redirect}")

    print(f"📧 等待邮箱验证链接（最多 {EMAIL_CODE_TIMEOUT} 秒）...")
    verify_url = get_verification_link(email, timeout=EMAIL_CODE_TIMEOUT)
    if not verify_url:
        print("❌ 未收到验证邮件")
        return None

    print(f"✅ 收到验证链接: {verify_url[:80]}...")
    verification_html, session_payload = _verify_email_link(session, verify_url)
    if verification_html is None:
        return None

    try:
        _complete_onboarding(session, session_payload)
    except Exception as exc:
        print(f"⚠️  onboarding/赠送点数协议流失败，继续尝试获取 API Key: {exc}")

    print("🔑 获取 API Key...")
    api_key = _get_api_key(session, verification_html)
    if not api_key:
        print("❌ 无法获取 API Key")
        return "SUCCESS_NO_KEY"

    print(f"✅ 获取到 API Key: {api_key[:20]}...")
    verify_result = verify_api_key(api_key)
    if verify_result is False:
        print("⚠️  API Key 验证失败，但仍然保存")
    elif verify_result is None:
        print("⚠️  API Key 可用性暂时无法确认，仍然保存")

    save_account(api_key)
    print("🎉 注册成功")
    print(f"   邮箱: {email}")
    print(f"   密码: {password}")
    print(f"   Key : {api_key}")
    return api_key


if __name__ == "__main__":
    from mail_provider import create_email

    email_address, account_password = create_email(service="firecrawl")
    register_with_api(email_address, account_password)
