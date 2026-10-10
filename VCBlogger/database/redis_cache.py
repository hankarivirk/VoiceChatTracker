"""Optional Redis/Upstash cache. It never stores the only copy of durable stats."""
import asyncio
import json
from typing import Any, Optional

from ..config import REDIS_URL
from ..utils.logging import logger

_client = None
_connect_lock = asyncio.Lock()
_last_error_at = 0.0


def is_configured() -> bool:
    return bool(REDIS_URL)


async def get_client():
    """Create a Redis client lazily; return None when Redis is not configured/reachable."""
    global _client, _last_error_at
    if not REDIS_URL:
        return None
    if _client is not None:
        return _client
    async with _connect_lock:
        if _client is not None:
            return _client
        # Avoid hammering an unavailable optional service.
        loop = asyncio.get_running_loop()
        if loop.time() - _last_error_at < 10:
            return None
        try:
            from redis.asyncio import Redis
            candidate = Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            await candidate.ping()
            _client = candidate
            logger.info("Redis cache connected.")
            return _client
        except Exception as exc:
            _last_error_at = loop.time()
            logger.warning("Redis cache unavailable; MongoDB will remain authoritative: %s", exc)
            try:
                await candidate.aclose()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            return None


async def cache_get_json(key: str) -> Optional[Any]:
    client = await get_client()
    if client is None:
        return None
    try:
        value = await client.get(key)
        return json.loads(value) if value else None
    except Exception as exc:
        logger.debug("Redis cache read failed: %s", exc)
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int = 10) -> bool:
    client = await get_client()
    if client is None:
        return False
    try:
        encoded = json.dumps(value, separators=(",", ":"), default=str)
        await client.set(key, encoded, ex=max(1, int(ttl_seconds)))
        return True
    except Exception as exc:
        logger.debug("Redis cache write failed: %s", exc)
        return False


async def increment_version(key: str) -> None:
    client = await get_client()
    if client is None:
        return
    try:
        await client.incr(key)
        await client.expire(key, 86400 * 30)
    except Exception as exc:
        logger.debug("Redis cache version update failed: %s", exc)


async def get_version(key: str) -> int:
    client = await get_client()
    if client is None:
        return 0
    try:
        value = await client.get(key)
        return int(value or 0)
    except Exception as exc:
        logger.debug("Redis cache version read failed: %s", exc)
        return 0


async def ping() -> bool:
    if not REDIS_URL:
        return False
    client = await get_client()
    if client is None:
        return False
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as exc:
            logger.debug("Redis close warning: %s", exc)
        _client = None
