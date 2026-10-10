"""Environment-based configuration for VCBlogger (Telegram-bot only)."""
import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Support running from the project root and running the package from its folder.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

CONFIG_ERRORS: list[str] = []


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parse a numeric environment setting and retain a clean startup diagnostic."""
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw or str(default))
    except (TypeError, ValueError):
        CONFIG_ERRORS.append(f"{name} must be an integer")
        value = default
    if minimum is not None and value < minimum:
        CONFIG_ERRORS.append(f"{name} must be at least {minimum}")
        value = minimum
    if maximum is not None and value > maximum:
        CONFIG_ERRORS.append(f"{name} must be at most {maximum}")
        value = maximum
    return value


# Required Telegram credentials (no safe automatic defaults exist).
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
API_ID: int = _env_int("API_ID", 0, minimum=0)
API_HASH: str = os.getenv("API_HASH", "").strip()
SESSION_STRING: str = os.getenv("SESSION_STRING", "").strip()
# Up to two additional logged-in Telegram user session strings. Comma-separated;
# use environment-variable storage only and never paste session strings into chats.
_EXTRA_SESSION_STRINGS = [x.strip() for x in os.getenv("ASSISTANT_SESSION_STRINGS", "").split(",") if x.strip()]
USER_SESSION_STRINGS: list[str] = list(dict.fromkeys(([SESSION_STRING] if SESSION_STRING else []) + _EXTRA_SESSION_STRINGS))[:3]

# Database URLs are auto-detected unless DB_PROVIDER explicitly selects one.
MONGO_URI: str = os.getenv("MONGO_URI", "").strip()
DATABASE_NAME: str = os.getenv("DATABASE_NAME", "vcblogger").strip() or "vcblogger"
REDIS_URL: str = os.getenv("REDIS_URL", "").strip()
# Prefer POSTGRES_URL, but honor POSTGRES_ARCHIVE_URL when POSTGRES_URL is unset OR empty.
POSTGRES_ARCHIVE_URL: str = (
    os.getenv("POSTGRES_URL", "").strip() or os.getenv("POSTGRES_ARCHIVE_URL", "").strip()
)
DB_PROVIDER: str = (os.getenv("DB_PROVIDER", "auto").strip().lower() or "auto")

_sudo_raw = os.getenv("SUDO_USERS", "").strip() or os.getenv("OWNER_ID", "").strip() or os.getenv("OWNER_IDS", "").strip()
SUDO_USERS: List[int] = [
    int(x.strip()) for x in _sudo_raw.split(",") if x.strip().isdigit()
]
MIN_DURATION_THRESHOLD: int = _env_int("MIN_DURATION_THRESHOLD", 5, minimum=0, maximum=86400)
SNAPSHOT_INTERVAL: int = _env_int("SNAPSHOT_INTERVAL", 10, minimum=5, maximum=3600)
AUTO_RECOVERY: bool = os.getenv("AUTO_RECOVERY", "true").lower() in ("true", "1", "yes")
VC_EVENT_NOTIFICATIONS: bool = os.getenv("VC_EVENT_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
VC_EVENT_MESSAGE_TTL_SECONDS: int = _env_int("VC_EVENT_MESSAGE_TTL_SECONDS", 10, minimum=0, maximum=3600)
SESSION_RETENTION_DAYS: int = _env_int("SESSION_RETENTION_DAYS", 90, minimum=30, maximum=3650)
INCIDENT_RETENTION_DAYS: int = _env_int("INCIDENT_RETENTION_DAYS", 30, minimum=7, maximum=3650)
ATTENDANCE_DEDUPE_RETENTION_DAYS: int = max(
    SESSION_RETENTION_DAYS,
    _env_int("ATTENDANCE_DEDUPE_RETENTION_DAYS", 180, minimum=30, maximum=3650),
)
STORAGE_LIMIT_MB: int = _env_int("STORAGE_LIMIT_MB", 0, minimum=0, maximum=1048576)
STORAGE_ALERT_INTERVAL_HOURS: int = _env_int("STORAGE_ALERT_INTERVAL_HOURS", 6, minimum=1, maximum=8760)
CLEANUP_INTERVAL_HOURS: int = _env_int("CLEANUP_INTERVAL_HOURS", 24, minimum=1, maximum=8760)
BACKUP_INTERVAL_HOURS: int = _env_int("BACKUP_INTERVAL_HOURS", 24, minimum=1, maximum=8760)
BACKUP_INITIAL_DELAY_SECONDS: int = _env_int("BACKUP_INITIAL_DELAY_SECONDS", 60, minimum=10, maximum=86400)
BACKUP_ENABLED: bool = os.getenv("BACKUP_ENABLED", "true").lower() in ("true", "1", "yes")
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "./backups").strip() or "./backups"
BACKUP_RETENTION_COUNT: int = _env_int("BACKUP_RETENTION_COUNT", 3, minimum=1, maximum=14)
LEADERBOARD_PAGE_SIZE: int = 10
STATS_TIMEZONE: str = os.getenv("STATS_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"
VC_STARTUP_DISCOVERY_LIMIT: int = _env_int("VC_STARTUP_DISCOVERY_LIMIT", 100, minimum=1, maximum=500)
VC_RECONCILE_INTERVAL_SECONDS: int = _env_int("VC_RECONCILE_INTERVAL_SECONDS", 15, minimum=15, maximum=3600)
APP_VERSION: str = "4.2.0"


def select_db_provider() -> str:
    """Choose a configured provider deterministically; never fail over silently."""
    configured = {
        "mongodb": bool(MONGO_URI),
        "postgres": bool(POSTGRES_ARCHIVE_URL),
        "redis": bool(REDIS_URL),
    }
    if DB_PROVIDER != "auto":
        return DB_PROVIDER if configured.get(DB_PROVIDER, False) else ""
    for name in ("mongodb", "postgres", "redis"):
        if configured[name]:
            return name
    return ""


def validate_required_config() -> None:
    """Fail early with actionable messages for incomplete or malformed settings."""
    missing = list(dict.fromkeys(CONFIG_ERRORS))
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if API_ID <= 0:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    # Assistant sessions are optional: Bot API commands must remain available even if
    # no user session is configured. Detailed VC participant tracking needs one.
    if DB_PROVIDER not in ("auto", "mongodb", "postgres", "redis"):
        missing.append("DB_PROVIDER must be auto, mongodb, postgres, or redis")

    selected_provider = select_db_provider()
    if selected_provider == "mongodb" and MONGO_URI and not MONGO_URI.startswith(("mongodb://", "mongodb+srv://")):
        missing.append("MONGO_URI must start with mongodb:// or mongodb+srv:// (copy the full driver connection string)")
    if selected_provider == "postgres" and POSTGRES_ARCHIVE_URL and not POSTGRES_ARCHIVE_URL.startswith(("postgres://", "postgresql://")):
        missing.append("POSTGRES_URL must start with postgres:// or postgresql://")
    if selected_provider == "redis" and REDIS_URL and not REDIS_URL.startswith(("redis://", "rediss://", "unix://")):
        missing.append("REDIS_URL must start with redis://, rediss://, or unix://")

    if not selected_provider:
        if DB_PROVIDER == "auto":
            missing.append("one database URL: MONGO_URI, POSTGRES_URL, or REDIS_URL")
        else:
            env_name = {"mongodb": "MONGO_URI", "postgres": "POSTGRES_URL", "redis": "REDIS_URL"}.get(DB_PROVIDER, "valid DB_PROVIDER")
            missing.append(f"{env_name} (selected by DB_PROVIDER={DB_PROVIDER})")
    if missing:
        raise RuntimeError("Missing or invalid environment configuration: " + ", ".join(dict.fromkeys(missing)))
