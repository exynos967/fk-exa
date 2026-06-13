"""
统一邮箱 provider 抽象。
当前支持：
1. Cloudflare 自定义邮件 API
2. DuckMail API
3. Cloud Mail (skymail) API
4. TempMail API
5. YYDS Mail (215.im) API
"""
import html
import random
import re
import string
import time

import requests as std_requests

from config import (
    CLOUD_MAIL_API_URL,
    CLOUD_MAIL_EMAIL,
    CLOUD_MAIL_PASSWORD,
    CLOUD_MAIL_DOMAIN,
    CLOUD_MAIL_DOMAINS,
    DUCKMAIL_API_KEY,
    DUCKMAIL_API_URL,
    DUCKMAIL_DOMAIN,
    DUCKMAIL_DOMAINS,
    EMAIL_API_TOKEN,
    EMAIL_API_URL,
    EMAIL_DOMAIN,
    EMAIL_DOMAINS,
    EMAIL_POLL_INTERVAL,
    EMAIL_PROVIDER,
    REQUEST_PROXIES,
    TEMPMAIL_API_KEY,
    TEMPMAIL_API_URL,
    TEMPMAIL_DOMAIN,
    TEMPMAIL_DOMAINS,
    TEMPMAIL_DOMAIN_PREFIX,
    TEMPMAIL_MODE,
    YYDS_API_KEY,
    YYDS_API_URL,
    YYDS_DOMAIN,
    YYDS_DOMAINS,
)

_DUCKMAIL_DOMAIN_PRIORITY = (
    "baldur.edu.kg",
    "duckmail.sbs",
)
_DUCKMAIL_DOMAIN_CACHE = None
_DUCKMAIL_MAILBOX_CACHE = {}
_CLOUD_MAIL_TOKEN_CACHE = None
_CLOUD_MAIL_MAILBOX_CACHE = {}
_TEMPMAIL_MAILBOX_CACHE = {}
_YYDS_MAILBOX_CACHE = {}
_SELECTED_DOMAIN = ""
_SUPPORTED_SERVICES = ("firecrawl", "exa")


def rand_str(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def get_configured_domains():
    """返回当前 provider 在配置里声明的可选域名。"""
    if EMAIL_PROVIDER == "duckmail":
        return DUCKMAIL_DOMAINS[:]
    if EMAIL_PROVIDER == "cloudmail":
        return CLOUD_MAIL_DOMAINS[:]
    if EMAIL_PROVIDER == "tempmail":
        return TEMPMAIL_DOMAINS[:]
    if EMAIL_PROVIDER == "yyds":
        return YYDS_DOMAINS[:]
    return EMAIL_DOMAINS[:]

def get_active_domain():
    """返回当前实际使用的域名。"""
    if _SELECTED_DOMAIN:
        return _SELECTED_DOMAIN

    configured = get_configured_domains()
    if configured:
        return configured[0]

    if EMAIL_PROVIDER == "duckmail":
        return DUCKMAIL_DOMAIN
    if EMAIL_PROVIDER == "cloudmail":
        return CLOUD_MAIL_DOMAIN
    if EMAIL_PROVIDER == "tempmail":
        return TEMPMAIL_DOMAIN
    if EMAIL_PROVIDER == "yyds":
        return YYDS_DOMAIN
    return EMAIL_DOMAIN

def set_selected_domain(domain):
    """设置本轮运行使用的域名。"""
    global _SELECTED_DOMAIN
    _SELECTED_DOMAIN = (domain or "").strip()


def _normalize_service(service):
    service = (service or "firecrawl").strip().lower()
    if service not in _SUPPORTED_SERVICES:
        return "firecrawl"
    return service


def _username_prefix(service):
    return rand_str(8)


def _generate_password():
    """生成不含 4 连续/重复字符的强密码，兼容 Firecrawl 密码规则。"""
    groups = (
        (string.ascii_uppercase.replace("I", "").replace("O", ""), string.ascii_lowercase),
        (string.digits.replace("0", "").replace("1", ""), "!@#$%*"),
        (string.ascii_uppercase.replace("I", "").replace("O", ""), string.ascii_lowercase),
        (string.digits.replace("0", "").replace("1", ""), "!@#$%*"),
        (string.ascii_uppercase.replace("I", "").replace("O", ""), string.ascii_lowercase),
        (string.digits.replace("0", "").replace("1", ""), "!@#$%*"),
    )
    return "".join(random.choice(pool) for pair in groups for pool in pair)


def create_email(service="firecrawl"):
    """按当前 provider 生成邮箱与强密码。"""
    password = _generate_password()
    prefix = _username_prefix(service)

    if EMAIL_PROVIDER == "duckmail":
        email = _create_duckmail_mailbox(password, prefix)
    elif EMAIL_PROVIDER == "cloudmail":
        email = _create_cloudmail_mailbox(password, prefix)
    elif EMAIL_PROVIDER == "tempmail":
        email = _create_tempmail_mailbox(password, prefix)
    elif EMAIL_PROVIDER == "yyds":
        email = _create_yyds_mailbox(password, prefix)
    else:
        username = f"{prefix}-{rand_str()}"
        email = f"{username}@{get_active_domain()}"

    print(f"✅ 邮箱({EMAIL_PROVIDER}): {email}")
    return email, password


def get_verification_link(email, timeout=120):
    """等待验证邮件并提取验证链接。"""
    print(f"⏳ 等待验证邮件（最多 {timeout} 秒）...", end="", flush=True)
    return _poll_mailbox(
        email=email,
        timeout=timeout,
        extractor=_extract_verification_link,
        found_message="\n✅ 找到验证链接",
        timeout_message="\n❌ 验证邮件超时",
        error_prefix="检查验证邮件失败",
        dot_progress=True,
    )


def get_email_code(email, timeout=120, service="firecrawl"):
    """等待邮箱里的 6 位验证码。"""
    print(f"📨 等待邮箱验证码（最多 {timeout} 秒）...")
    found_message = "✅ 收到验证码" if _normalize_service(service) == "exa" else "✅ 收到 6 位验证码"
    return _poll_mailbox(
        email=email,
        timeout=timeout,
        extractor=lambda message: _extract_email_code(message, service=service),
        found_message=found_message,
        timeout_message="❌ 等待邮箱验证码超时",
        error_prefix="读取邮箱验证码失败",
        dot_progress=False,
    )


def _poll_mailbox(email, timeout, extractor, found_message, timeout_message, error_prefix, dot_progress):
    start_time = time.time()
    seen_ids = set()

    while time.time() - start_time < timeout:
        try:
            for message in _iter_messages(email):
                message_id = _message_id(message)
                if message_id and message_id in seen_ids:
                    continue
                if message_id:
                    seen_ids.add(message_id)

                result = extractor(message)
                if result:
                    print(found_message)
                    return result
        except Exception as exc:
            print(f"⚠️  {error_prefix}: {exc}")

        time.sleep(EMAIL_POLL_INTERVAL)
        if dot_progress:
            print(".", end="", flush=True)

    print(timeout_message)
    return None


def _extract_verification_link(message):
    subject = (message.get("subject") or "").lower()
    sender = (message.get("from") or message.get("message_from") or "").lower()
    content = _message_content(message)
    urls = [
        html.unescape(raw).rstrip(").,;")
        for raw in re.findall(r'https://[^\s<>"\']+', content, re.IGNORECASE)
    ]

    primary_link_hints = ("verif", "confirm", "magic", "auth", "callback", "signin", "signup")
    primary_host_hints = ("firecrawl", "clerk", "stytch", "auth", "login")
    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in primary_link_hints) and any(host in lowered for host in primary_host_hints):
            return url

    combined = f"{sender} {subject} {content[:4000]}".lower()
    message_hints = ("verify", "verification", "confirm", "magic link", "sign in", "firecrawl")
    if not any(token in combined for token in message_hints):
        return None

    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in primary_link_hints):
            return url

    return None


def _extract_email_code(message, service="firecrawl"):
    service = _normalize_service(service)
    subject = (message.get("subject") or "").lower()
    text = message.get("text") or ""
    content = _message_content(message)
    combined = f"{subject}\n{content}".lower()

    if service == "exa":
        if "exa" not in combined:
            return None
        if "verification code" not in combined and "sign in" not in combined:
            return None
        for source in (text, content):
            match = re.search(
                r"verification code(?:\s+for\s+exa)?(?:\s+is)?[^A-Z0-9]*([A-Z0-9]{6})",
                source,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
    else:
        if "verify your identity" not in subject and "verify" not in subject:
            return None

    for source in (text, content):
        match = re.search(r"\b(\d{6})\b", source)
        if match:
            return match.group(1)
    return None


def _iter_messages(email):
    if EMAIL_PROVIDER == "duckmail":
        yield from _duckmail_iter_messages(email)
        return
    if EMAIL_PROVIDER == "cloudmail":
        yield from _cloudmail_iter_messages(email)
        return
    if EMAIL_PROVIDER == "tempmail":
        yield from _tempmail_iter_messages(email)
        return
    if EMAIL_PROVIDER == "yyds":
        yield from _yyds_iter_messages(email)
        return

    yield from _cloudflare_iter_messages(email)


def _cloudflare_iter_messages(email):
    response = std_requests.get(
        f"{EMAIL_API_URL}/messages",
        params={"address": email},
        headers={"Authorization": f"Bearer {EMAIL_API_TOKEN}"},
        proxies=REQUEST_PROXIES,
        timeout=10,
    )
    response.raise_for_status()

    for message in response.json().get("messages", []):
        yield message


def _duckmail_iter_messages(email):
    token = _duckmail_get_token(email)
    response = _duckmail_request("GET", "/messages", token=token)

    if response.status_code == 401:
        token = _duckmail_get_token(email, refresh=True)
        response = _duckmail_request("GET", "/messages", token=token)

    response.raise_for_status()

    for message in response.json().get("hydra:member", []):
        message_id = message.get("id")
        if not message_id:
            continue

        detail = _duckmail_request("GET", f"/messages/{message_id}", token=token)
        if detail.status_code == 401:
            token = _duckmail_get_token(email, refresh=True)
            detail = _duckmail_request("GET", f"/messages/{message_id}", token=token)
        detail.raise_for_status()
        yield detail.json()


def _create_duckmail_mailbox(password, prefix):
    domain = _choose_duckmail_domain()

    for _ in range(5):
        username = f"{prefix}-{rand_str()}"
        email = f"{username}@{domain}"
        response = _duckmail_request(
            "POST",
            "/accounts",
            json={"address": email, "password": password},
            use_api_key=True,
        )

        if response.status_code == 201:
            account = response.json()
            token = _duckmail_issue_token(email, password)
            _DUCKMAIL_MAILBOX_CACHE[email] = {
                "account_id": account.get("id", ""),
                "password": password,
                "token": token,
            }
            return email

        if response.status_code not in (409, 422):
            response.raise_for_status()

        message = _response_error_message(response).lower()
        if "exists" in message or "already" in message or response.status_code == 409:
            continue

        raise RuntimeError(f"DuckMail 创建邮箱失败: {_response_error_message(response)}")

    raise RuntimeError("DuckMail 邮箱创建失败：随机地址重复次数过多")


def _choose_duckmail_domain():
    domains = _duckmail_domains()
    selected = get_active_domain()
    configured = get_configured_domains()

    if selected:
        if selected not in domains:
            raise RuntimeError(
                f"配置的 DuckMail 域名不可用: {selected}，当前可用域名: {', '.join(domains)}"
            )
        return selected

    for domain in configured:
        if domain in domains:
            return domain

    for domain in _DUCKMAIL_DOMAIN_PRIORITY:
        if domain in domains:
            return domain

    return domains[0]


def _duckmail_domains():
    global _DUCKMAIL_DOMAIN_CACHE
    if _DUCKMAIL_DOMAIN_CACHE is not None:
        return _DUCKMAIL_DOMAIN_CACHE

    response = _duckmail_request("GET", "/domains", use_api_key=True)
    response.raise_for_status()
    domains = [
        item.get("domain")
        for item in response.json().get("hydra:member", [])
        if item.get("domain")
    ]

    if not domains:
        raise RuntimeError("DuckMail 未返回可用域名")

    _DUCKMAIL_DOMAIN_CACHE = domains
    return domains


def _duckmail_get_token(email, refresh=False):
    mailbox = _DUCKMAIL_MAILBOX_CACHE.get(email)
    if not mailbox:
        raise RuntimeError("DuckMail 邮箱上下文不存在，请重新生成邮箱后再试")

    if mailbox.get("token") and not refresh:
        return mailbox["token"]

    mailbox["token"] = _duckmail_issue_token(email, mailbox["password"])
    return mailbox["token"]


def _duckmail_issue_token(email, password):
    response = _duckmail_request(
        "POST",
        "/token",
        json={"address": email, "password": password},
    )
    response.raise_for_status()

    token = response.json().get("token")
    if not token:
        raise RuntimeError("DuckMail 登录成功但未返回 token")
    return token


def _duckmail_request(method, path, token=None, use_api_key=False, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif use_api_key and DUCKMAIL_API_KEY:
        headers["Authorization"] = f"Bearer {DUCKMAIL_API_KEY}"

    if "json" in kwargs:
        headers.setdefault("Content-Type", "application/json")

    return std_requests.request(
        method,
        f"{DUCKMAIL_API_URL.rstrip('/')}{path}",
        headers=headers,
        proxies=REQUEST_PROXIES,
        timeout=kwargs.pop("timeout", 15),
        **kwargs,
    )


# ──────────────────────────────────────────────
# TempMail provider
# ──────────────────────────────────────────────

def _normalize_tempmail_mode():
    mode = (TEMPMAIL_MODE or "auto").strip().lower()
    if mode in {"single", "multi"}:
        return mode
    return "auto"


def _randomize_tempmail_pattern(pattern):
    return ".".join(
        "".join(
            random.choice(string.ascii_lowercase + string.digits) if char == "*" else char
            for char in segment
        )
        for segment in pattern.split(".")
        if segment
    )


def _resolve_tempmail_domain(base_domain):
    base_domain = (base_domain or "").strip()
    pattern = (TEMPMAIL_DOMAIN_PREFIX or "").strip()

    if not pattern:
        mode = _normalize_tempmail_mode()
        return {
            "mode": "single" if mode == "auto" else mode,
            "domain": base_domain,
            "subdomain": None,
        }

    resolved = _randomize_tempmail_pattern(pattern)
    if re.fullmatch(r"[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*", resolved):
        return {
            "mode": "multi",
            "domain": base_domain,
            "subdomain": resolved,
        }

    return {
        "mode": "single",
        "domain": f"{resolved}.{base_domain}",
        "subdomain": None,
    }


def _tempmail_request(method, path, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = f"Bearer {TEMPMAIL_API_KEY}"
    if "json" in kwargs:
        headers.setdefault("Content-Type", "application/json")

    return std_requests.request(
        method,
        f"{TEMPMAIL_API_URL.rstrip('/')}{path}",
        headers=headers,
        proxies=REQUEST_PROXIES,
        timeout=kwargs.pop("timeout", 15),
        **kwargs,
    )


def _tempmail_extract_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("data", "mailboxes", "emails", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _tempmail_extract_mailbox(payload):
    if not isinstance(payload, dict):
        return {}
    mailbox = payload.get("mailbox")
    if isinstance(mailbox, dict):
        return mailbox
    return payload


def _tempmail_extract_email(payload):
    if not isinstance(payload, dict):
        return {}
    email = payload.get("email")
    if isinstance(email, dict):
        return email
    return payload


def _tempmail_mailbox_address(mailbox):
    if not isinstance(mailbox, dict):
        return ""

    full_address = (
        mailbox.get("full_address")
        or mailbox.get("fullAddress")
        or mailbox.get("email")
    )
    if full_address:
        return str(full_address).strip()

    address = mailbox.get("address")
    domain = mailbox.get("domain")
    if address and domain:
        return f"{address}@{domain}"
    if address and "@" in str(address):
        return str(address).strip()
    return ""


def _tempmail_mailbox_id(mailbox):
    if not isinstance(mailbox, dict):
        return ""
    mailbox_id = mailbox.get("id") or mailbox.get("mailbox_id") or mailbox.get("mailboxId")
    return str(mailbox_id).strip() if mailbox_id else ""


def _tempmail_normalize_message(message):
    if not isinstance(message, dict):
        return {"text": str(message)}

    sender = (
        message.get("sender")
        or message.get("from_addr")
        or message.get("fromAddress")
        or message.get("from")
        or ""
    )
    text = message.get("body_text") or message.get("text_body") or message.get("text") or ""
    html_body = message.get("body_html") or message.get("html_body") or message.get("html") or ""

    normalized = dict(message)
    normalized.setdefault("id", message.get("email_id") or message.get("emailId"))
    normalized["from"] = sender
    normalized["message_from"] = sender
    normalized["text"] = text
    normalized["html"] = html_body
    return normalized


def _tempmail_create_mailbox_payload(prefix):
    username = f"{prefix}-{rand_str()}"
    selected_domain = get_active_domain()
    payload = {"address": username}
    if selected_domain:
        resolved = _resolve_tempmail_domain(selected_domain)
        payload["domain"] = resolved["domain"]
        payload["mode"] = resolved["mode"]
        if resolved["subdomain"]:
            payload["subdomain"] = resolved["subdomain"]
    else:
        mode = _normalize_tempmail_mode()
        if mode != "auto":
            payload["mode"] = mode

    return payload


def _create_tempmail_mailbox(_password, prefix):
    for _ in range(5):
        payload = _tempmail_create_mailbox_payload(prefix)
        response = _tempmail_request("POST", "/api/mailboxes", json=payload)

        if response.status_code in (200, 201):
            mailbox = _tempmail_extract_mailbox(response.json())
            email = _tempmail_mailbox_address(mailbox)
            mailbox_id = _tempmail_mailbox_id(mailbox)
            if not email or not mailbox_id:
                raise RuntimeError("TempMail 创建邮箱成功，但响应缺少邮箱地址或 mailbox id")

            _TEMPMAIL_MAILBOX_CACHE[email] = {
                "id": mailbox_id,
                "full_address": email,
            }
            return email

        if response.status_code == 409:
            continue
        if response.status_code == 400:
            raise RuntimeError(f"TempMail 域名不存在或未激活: {_response_error_message(response)}")
        if response.status_code == 503:
            raise RuntimeError("TempMail 当前无可用域名")
        response.raise_for_status()

    raise RuntimeError("TempMail 邮箱创建失败：随机地址重复次数过多")


def _tempmail_get_mailbox_context(email, refresh=False):
    mailbox = _TEMPMAIL_MAILBOX_CACHE.get(email)
    if mailbox and not refresh:
        return mailbox

    response = _tempmail_request("GET", "/api/mailboxes", params={"page": 1, "size": 100})
    response.raise_for_status()

    for item in _tempmail_extract_list(response.json()):
        full_address = _tempmail_mailbox_address(item)
        if full_address.lower() != email.lower():
            continue

        mailbox = {
            "id": _tempmail_mailbox_id(item),
            "full_address": full_address,
        }
        _TEMPMAIL_MAILBOX_CACHE[email] = mailbox
        return mailbox

    raise RuntimeError(f"TempMail 未找到邮箱上下文: {email}")


def _tempmail_iter_messages(email):
    try:
        mailbox = _tempmail_get_mailbox_context(email)
        mailbox_id = mailbox["id"]
        response = _tempmail_request(
            "GET",
            f"/api/mailboxes/{mailbox_id}/emails",
            params={"page": 1, "size": 20},
        )
        response.raise_for_status()

        for summary in _tempmail_extract_list(response.json()):
            email_id = _message_id(summary)
            detail = summary

            if email_id:
                detail_response = _tempmail_request(
                    "GET",
                    f"/api/mailboxes/{mailbox_id}/emails/{email_id}",
                )
                if detail_response.status_code == 200:
                    detail = _tempmail_extract_email(detail_response.json())

            yield _tempmail_normalize_message(detail)
    except Exception:
        return


# ──────────────────────────────────────────────
# Cloud Mail (skymail) provider
# ──────────────────────────────────────────────

def _cloudmail_get_token():
    """获取或刷新 Cloud Mail 管理员 Token。"""
    global _CLOUD_MAIL_TOKEN_CACHE
    if _CLOUD_MAIL_TOKEN_CACHE:
        return _CLOUD_MAIL_TOKEN_CACHE

    response = std_requests.post(
        f"{CLOUD_MAIL_API_URL.rstrip('/')}/api/public/genToken",
        json={"email": CLOUD_MAIL_EMAIL, "password": CLOUD_MAIL_PASSWORD},
        proxies=REQUEST_PROXIES,
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200:
        raise RuntimeError(f"Cloud Mail 生成 Token 失败: {data.get('message', response.text)}")

    token = data.get("data", {}).get("token")
    if not token:
        raise RuntimeError("Cloud Mail 返回 Token 为空")
    _CLOUD_MAIL_TOKEN_CACHE = token
    return token


def _cloudmail_request(method, path, json=None, timeout=15):
    """Cloud Mail API 统一请求封装。"""
    token = _cloudmail_get_token()
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    return std_requests.request(
        method,
        f"{CLOUD_MAIL_API_URL.rstrip('/')}{path}",
        headers=headers,
        json=json,
        proxies=REQUEST_PROXIES,
        timeout=timeout,
    )


def _create_cloudmail_mailbox(password, prefix):
    """通过 Cloud Mail addUser 创建邮箱。"""
    domain = get_active_domain()

    for _ in range(5):
        username = f"{prefix}-{rand_str()}"
        email = f"{username}@{domain}"
        response = _cloudmail_request(
            "POST",
            "/api/public/addUser",
            json={"list": [{"email": email, "password": password}]},
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200:
                _CLOUD_MAIL_MAILBOX_CACHE[email] = {
                    "password": password,
                }
                return email

        if response.status_code not in (409, 422, 400):
            response.raise_for_status()

        message = _response_error_message(response).lower()
        if "exists" in message or "already" in message:
            continue

        raise RuntimeError(f"Cloud Mail 创建邮箱失败: {_response_error_message(response)}")

    raise RuntimeError("Cloud Mail 邮箱创建失败：随机地址重复次数过多")


def _cloudmail_iter_messages(email):
    """通过 Cloud Mail emailList 轮询指定邮箱的收件。"""
    try:
        response = _cloudmail_request(
            "POST",
            "/api/public/emailList",
            json={
                "toEmail": email,
                "timeSort": "desc",
                "type": 0,
                "num": 1,
                "size": 20,
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 200:
            return

        for message in data.get("data", []):
            # Cloud Mail 用 sendName 做发件人名字，补上 from 字段兼容提取逻辑
            message.setdefault("from", message.get("sendEmail", ""))
            yield message
    except Exception:
        return


# ──────────────────────────────────────────────
# YYDS Mail (215.im) provider
# ──────────────────────────────────────────────

def _yyds_create_request(json_body, timeout=15):
    """用 API Key 发 YYDS 请求。"""
    return std_requests.post(
        f"{YYDS_API_URL}/accounts",
        json=json_body,
        headers={
            "X-API-Key": YYDS_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )


def _yyds_auth_request(method, path, token, **kwargs):
    """用 temp token 发 YYDS 请求。"""
    return std_requests.request(
        method,
        f"{YYDS_API_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=kwargs.pop("timeout", 15),
        **kwargs,
    )


def _create_yyds_mailbox(password, prefix):
    """通过 YYDS API 创建临时邮箱。"""
    domain = get_active_domain()

    for _ in range(5):
        local_part = f"{prefix}-{rand_str()}"
        response = _yyds_create_request({
            "localPart": local_part,
            "domain": domain,
        })

        if response.status_code in (200, 201):
            data = response.json()
            if data.get("success"):
                account = data["data"]
                email = account["address"]
                token = account["token"]
                _YYDS_MAILBOX_CACHE[email] = {
                    "password": password,
                    "token": token,
                }
                return email

        if response.status_code not in (400, 409, 422):
            response.raise_for_status()

        message = _response_error_message(response).lower()
        if "exists" in message or "already" in message or "duplicate" in message:
            continue

        raise RuntimeError(f"YYDS 创建邮箱失败: {_response_error_message(response)}")

    raise RuntimeError("YYDS 邮箱创建失败：随机地址重复次数过多")


def _yyds_iter_messages(email):
    """通过 YYDS API 轮询邮件。"""
    mailbox = _YYDS_MAILBOX_CACHE.get(email)
    if not mailbox:
        return

    token = mailbox["token"]
    try:
        # 列出消息
        response = _yyds_auth_request(
            "GET",
            f"/messages?address={email}",
            token,
            timeout=10,
        )
        if response.status_code != 200:
            return
        data = response.json()
        if not data.get("success"):
            return

        messages = data.get("data", {}).get("messages", [])
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id:
                continue

            # 获取消息详情（含 body）
            detail = _yyds_auth_request(
                "GET",
                f"/messages/{msg_id}?address={email}",
                token,
                timeout=10,
            )
            if detail.status_code != 200:
                # 没有详情就用列表数据
                msg["id"] = msg_id
                msg.setdefault("msgid", msg_id)
                msg.setdefault("from", msg.get("from", {}).get("address", ""))
                msg.setdefault("subject", msg.get("subject", ""))
                yield msg
                continue

            detail_data = detail.json()
            if detail_data.get("success"):
                full = detail_data["data"]
                full["id"] = full.get("id", msg_id)
                full.setdefault("msgid", full["id"])
                full.setdefault("from", (full.get("from") or {}).get("address", ""))
                # html 可能是数组，join 一下
                html_val = full.get("html")
                if isinstance(html_val, list):
                    full["html"] = " ".join(html_val)
                yield full
    except Exception:
        return


def _message_id(message):
    return message.get("id") or message.get("msgid") or message.get("emailId")


def _message_content(message):
    html = message.get("html") or ""
    if isinstance(html, list):
        html = " ".join(str(item) for item in html)
    text = message.get("text") or ""
    return f"{html} {text}"


def _response_error_message(response):
    try:
        data = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"

    if isinstance(data, dict):
        return data.get("message") or data.get("detail") or data.get("error") or str(data)
    return str(data)
