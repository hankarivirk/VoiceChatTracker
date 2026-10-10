"""Optional Neon/PostgreSQL archive for durable copies of session/attendance events.

MongoDB remains the source of truth for live commands. Failures in the optional archive
are logged and do not stop voice tracking; callers should not claim a mirror is healthy
unless ping_archive() succeeds.
"""
import asyncio
import json
from typing import Any, Optional

from ..config import POSTGRES_ARCHIVE_URL
from ..utils.logging import logger

_pool = None
_connect_lock = asyncio.Lock()
_last_failure_at = 0.0
_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vcblogger_archive (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    group_id BIGINT NOT NULL,
    event_ts DOUBLE PRECISION NOT NULL,
    payload JSONB NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vcblogger_archive_group_ts
    ON vcblogger_archive (group_id, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_vcblogger_archive_type_ts
    ON vcblogger_archive (event_type, event_ts DESC);
"""


def is_configured() -> bool:
    return bool(POSTGRES_ARCHIVE_URL)


async def get_pool():
    global _pool, _last_failure_at
    if not POSTGRES_ARCHIVE_URL:
        return None
    if _pool is not None:
        return _pool
    async with _connect_lock:
        if _pool is not None:
            return _pool
        loop = asyncio.get_running_loop()
        if loop.time() - _last_failure_at < 15:
            return None
        try:
            import asyncpg
            pool = await asyncpg.create_pool(
                dsn=POSTGRES_ARCHIVE_URL,
                min_size=1,
                max_size=3,
                command_timeout=5,
                timeout=5,
            )
            async with pool.acquire() as conn:
                # Execute statements separately because asyncpg's execute may reject a
                # multi-command prepared statement on some server versions.
                for statement in _TABLE_SQL.split(";"):
                    if statement.strip():
                        await conn.execute(statement)
                await conn.fetchval("SELECT 1")
            _pool = pool
            logger.info("PostgreSQL archive connected and schema verified.")
            return _pool
        except Exception as exc:
            _last_failure_at = loop.time()
            logger.warning("Optional PostgreSQL archive unavailable: %s", exc)
            try:
                if "pool" in locals():
                    await pool.close()
            except Exception:
                pass
            return None


async def archive_event(
    event_id: str,
    event_type: str,
    group_id: int,
    event_ts: float,
    payload: dict[str, Any],
) -> bool:
    """Idempotently upsert one event into the optional archive."""
    pool = await get_pool()
    if pool is None:
        return False
    try:
        encoded = json.dumps(payload, separators=(",", ":"), default=str)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO vcblogger_archive(event_id, event_type, group_id, event_ts, payload)
                VALUES($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT(event_id) DO UPDATE SET
                    event_type=EXCLUDED.event_type,
                    group_id=EXCLUDED.group_id,
                    event_ts=EXCLUDED.event_ts,
                    payload=EXCLUDED.payload
                """,
                str(event_id), str(event_type), int(group_id), float(event_ts), encoded,
            )
        return True
    except Exception as exc:
        logger.warning("PostgreSQL archive write failed for %s: %s", event_type, exc)
        return False


async def ping() -> bool:
    if not POSTGRES_ARCHIVE_URL:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1
    except Exception:
        return False


async def close() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:
            logger.debug("PostgreSQL archive close warning: %s", exc)
        _pool = None


async def clear_archive_data() -> bool:
    """Clear the optional archive during an explicitly confirmed owner wipe."""
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM vcblogger_archive")
        return True
    except Exception as exc:
        logger.warning("Could not clear PostgreSQL archive: %s", exc)
        return False


async def delete_user_archive_data(user_id: int) -> bool:
    """Delete archived event payloads that explicitly belong to a user."""
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM vcblogger_archive WHERE payload->>'user_id' = $1 OR payload->'participants' @> $2::jsonb",
                str(int(user_id)), json.dumps([{"user_id": int(user_id)}])
            )
        return True
    except Exception as exc:
        logger.warning("Could not clear PostgreSQL archive data for user %s: %s", user_id, exc)
        return False
