"""
注册器配置
优先读取环境变量；若项目根目录存在 .env，则先载入。
"""
import os
from pathlib import Path

PLACEHOLDER_ENV_VALUES = {
    "EMAIL_API_URL": {"https://your-mail-api.example.com"},
    "EMAIL_API_TOKEN": {"replace-with-your-token"},
    "EMAIL_DOMAIN": {"example.com"},
    "EMAIL_DOMAINS": {"example.com", "example.org"},
    "DUCKMAIL_DOMAIN": {"example.com"},
    "DUCKMAIL_DOMAINS": {"example.com", "example.org"},
    "CLOUD_MAIL_DOMAIN": {"example.com"},
    "CLOUD_MAIL_DOMAINS": {"example.com", "example.org"},
    "TEMPMAIL_API_KEY": {"replace-with-your-token"},
    "TEMPMAIL_DOMAIN": {"example.com"},
    "TEMPMAIL_DOMAINS": {"example.com", "example.org"},
    "SERVER_URL": {"https://your-server.example.com"},
    "SERVER_ADMIN_PASSWORD": {"replace-with-your-admin-password"},
}


def _load_dotenv():
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _get_str(name, default=""):
    return os.getenv(name, default).strip()


def _get_int(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)

def _get_list(name, fallback=""):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        value = fallback
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_placeholder_env_value(name, value):
    normalized = (value or "").strip()
    if not normalized:
        return False

    normalized_lower = normalized.lower()
    placeholder_values = {item.lower() for item in PLACEHOLDER_ENV_VALUES.get(name, set())}
    if normalized_lower in placeholder_values:
        return True

    if normalized_lower.startswith("replace-with-"):
        return True

    if normalized_lower in {"example.com", "example.org"}:
        return True

    if normalized_lower.startswith("https://your-") and ".example.com" in normalized_lower:
        return True

    return False


_load_dotenv()

# 邮箱配置
EMAIL_PROVIDER = _get_str("EMAIL_PROVIDER", "cloudflare").lower()
SUPPORTED_EMAIL_PROVIDERS = ("cloudflare", "duckmail", "cloudmail", "tempmail")
EMAIL_API_URL = _get_str("EMAIL_API_URL")
EMAIL_API_TOKEN = _get_str("EMAIL_API_TOKEN")
EMAIL_DOMAIN = _get_str("EMAIL_DOMAIN")
EMAIL_DOMAINS = _get_list("EMAIL_DOMAINS", EMAIL_DOMAIN)
DUCKMAIL_API_URL = _get_str("DUCKMAIL_API_URL", "https://api.duckmail.sbs")
DUCKMAIL_API_KEY = _get_str("DUCKMAIL_API_KEY")
DUCKMAIL_DOMAIN = _get_str("DUCKMAIL_DOMAIN")
DUCKMAIL_DOMAINS = _get_list("DUCKMAIL_DOMAINS", DUCKMAIL_DOMAIN)

# Cloud Mail 配置
CLOUD_MAIL_API_URL = _get_str("CLOUD_MAIL_API_URL", "https://mail.skymail.ink")
CLOUD_MAIL_EMAIL = _get_str("CLOUD_MAIL_EMAIL")
CLOUD_MAIL_PASSWORD = _get_str("CLOUD_MAIL_PASSWORD")
CLOUD_MAIL_DOMAIN = _get_str("CLOUD_MAIL_DOMAIN")
CLOUD_MAIL_DOMAINS = _get_list("CLOUD_MAIL_DOMAINS", CLOUD_MAIL_DOMAIN)
TEMPMAIL_API_URL = _get_str("TEMPMAIL_API_URL", "https://tempmail.futureppo.top")
TEMPMAIL_API_KEY = _get_str("TEMPMAIL_API_KEY")
TEMPMAIL_DOMAIN = _get_str("TEMPMAIL_DOMAIN")
TEMPMAIL_DOMAINS = _get_list("TEMPMAIL_DOMAINS", TEMPMAIL_DOMAIN)
TEMPMAIL_MODE = _get_str("TEMPMAIL_MODE", "auto").lower()
TEMPMAIL_DOMAIN_PREFIX = _get_str("TEMPMAIL_DOMAIN_PREFIX")

# 上传目标
SERVER_URL = _get_str("SERVER_URL")
SERVER_ADMIN_PASSWORD = _get_str("SERVER_ADMIN_PASSWORD")

# 注册默认参数
DEFAULT_COUNT = _get_int("DEFAULT_COUNT", 5)
DEFAULT_CONCURRENCY = _get_int("DEFAULT_CONCURRENCY", 2)
DEFAULT_DELAY = _get_int("DEFAULT_DELAY", 10)
DEFAULT_UPLOAD = _get_bool("DEFAULT_UPLOAD", True)

EMAIL_CODE_TIMEOUT = _get_int("EMAIL_CODE_TIMEOUT", 90)
API_KEY_TIMEOUT = _get_int("API_KEY_TIMEOUT", 20)
EMAIL_POLL_INTERVAL = _get_int("EMAIL_POLL_INTERVAL", 3)

# 外置 Turnstile Solver
TURNSTILE_SOLVER_URL = _get_str("TURNSTILE_SOLVER_URL", "http://127.0.0.1:5000")
