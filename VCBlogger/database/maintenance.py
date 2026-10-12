"""Non-destructive history preservation and storage guardrails.

Historical VC sessions, incidents, attendance rows, and voice-time ledger entries
are retained for lifetime statistics. The cleanup task only records storage status.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

from ..config import (DATABASE_NAME, select_db_provider,
                      STORAGE_LIMIT_MB, STORAGE_ALERT_INTERVAL_HOURS, CLEANUP_INTERVAL_HOURS, SUDO_USERS)
from ..utils.logging import logger
from .mongo import get_db

_cleanup_lock = asyncio.Lock()
_MIB = 1024 * 1024
_last_alert_level = 0


def current_month_start_ts() -> float:
    """Compatibility helper for tests and diagnostics."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()




async def storage_usage_bytes() -> int | None:
    """Return provider-reported approximate usage where the provider exposes it."""
    provider = select_db_provider()
    try:
        db = await get_db()
        if provider == "mongodb":
            from . import mongo as mongo_module
            stats = await mongo_module._client[DATABASE_NAME].command("dbStats", scale=1)
            return int(stats.get("dataSize", 0) + stats.get("indexSize", 0))
        if provider == "postgres":
            async with db.pool.acquire() as conn:
                size = await conn.fetchval("SELECT pg_database_size(current_database())")
                return int(size or 0)
        if provider == "redis":
            info = await db.client.info("memory")
            return int(info.get("used_memory", 0))
    except Exception as exc:
        logger.debug("Storage usage query unavailable: %s", exc)
    return None


async def run_retention_cleanup(force: bool = False) -> dict[str, int | str | None]:
    """Report storage state without deleting user history.

    VCBlogger's configured policy is lifetime history preservation. This function is
    intentionally non-destructive: `force` never enables deletion, compaction, or
    removal of old session, incident, attendance, or voice-time ledger records.
    """
    async with _cleanup_lock:
        usage = await storage_usage_bytes()
        result: dict[str, int | str | None] = {
            "provider": select_db_provider(),
            "history_deletion": "disabled",
            "usage_bytes_before": usage,
            "usage_bytes_after": usage,
            "sessions_deleted": 0,
            "incidents_deleted": 0,
            "attendance_deleted": 0,
            "voice_segments_compacted": 0,
            "history_records_preserved": True,
        }
        db = await get_db()
        try:
            await db["maintenance_state"].update_one(
                {"_id": "retention_status"},
                {"$set": {
                    "last_cleanup_at": datetime.now(timezone.utc).timestamp(),
                    "last_result": result,
                    "history_deletion_enabled": False,
                    "last_cleanup_mode": "non_destructive_history_preservation",
                }},
                upsert=True,
            )
        except Exception as exc:
            logger.warning("Could not record non-destructive history-preservation status: %s", exc)
        logger.info("History cleanup skipped: lifetime record preservation is enabled.")
        return result


async def retention_loop() -> None:
    """Run daily to record storage status; never delete historical user records."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
            await run_retention_cleanup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Storage retention/compaction failed; will retry next day: %s", exc)


async def storage_alert_loop(bot) -> None:
    """Warn configured owner accounts as usage approaches a soft guardrail."""
    global _last_alert_level
    first_check = True
    while True:
        try:
            # Check once immediately after startup, then at the configured interval.
            if not first_check:
                await asyncio.sleep(STORAGE_ALERT_INTERVAL_HOURS * 3600)
            first_check = False
            usage = await storage_usage_bytes()
            if usage is None:
                continue
            provider = select_db_provider() or "unknown"
            configured_mb = STORAGE_LIMIT_MB
            if configured_mb <= 0:
                # Conservative defaults only; actual provider quotas must be checked separately.
                configured_mb = 100 if provider == "redis" else 400
            ratio = usage / (configured_mb * _MIB)
            level = 3 if ratio >= .90 else 2 if ratio >= .80 else 1 if ratio >= .70 else 0
            if level == 0:
                if ratio < .60: _last_alert_level = 0
                continue
            if level <= _last_alert_level:
                continue
            _last_alert_level = level
            pct = int(ratio * 100)
            msg = (f"Database storage is {pct}% full ({provider}, about {usage / _MIB:.0f} of "
                   f"{configured_mb} MiB).\nCheck your database dashboard.")
            logger.warning(msg)
            if not SUDO_USERS:
                logger.warning("Set SUDO_USERS to receive database storage warnings in Telegram DMs.")
            for user_id in SUDO_USERS:
                try:
                    await bot.send_message(user_id, msg)
                except Exception as exc:
                    logger.warning("Could not DM storage warning to sudo user %s: %s", user_id, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Storage alert check failed: %s", exc)
