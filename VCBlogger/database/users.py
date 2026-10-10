"""User VC stats in MongoDB, short-TTL Redis cache, and optional Neon event archive."""
import uuid
from typing import Optional, Dict, Any, List
from .mongo import get_db
from .redis_cache import cache_get_json, cache_set_json, increment_version, get_version
from .postgres_archive import archive_event
from ..utils.time_utils import now_ts
from ..utils.logging import logger


def _cache_version_key(group_id: Optional[int]) -> str:
    return "vcblogger:stats-version:all" if group_id is None else f"vcblogger:stats-version:group:{int(group_id)}"


async def get_or_create_user(user_id: int, first_name: str = "", username: str = "") -> Dict[str, Any]:
    db = await get_db()
    users_col = db["users"]
    user = await users_col.find_one({"user_id": int(user_id)})
    if not user:
        user = {
            "user_id": int(user_id),
            "first_name": first_name or f"User_{user_id}",
            "username": username or "",
            "total_duration": 0,
            "sessions_count": 0,
            "created_at": now_ts(),
            "last_active": now_ts(),
            "group_durations": {},
            "group_sessions": {},
            "legacy_total_duration": 0,
            "legacy_group_durations": {},
            "ledger_migrated_at": now_ts(),
        }
        await users_col.update_one({"user_id": int(user_id)}, {"$set": user}, upsert=True)
    else:
        updates = {}
        if first_name and user.get("first_name") != first_name: updates["first_name"] = first_name
        if username and user.get("username") != username: updates["username"] = username
        if updates:
            await users_col.update_one({"user_id": int(user_id)}, {"$set": updates})
            user.update(updates)
        # Migrate older user docs without group session counters.
        if "group_sessions" not in user:
            await users_col.update_one({"user_id": int(user_id)}, {"$set": {"group_sessions": {}}})
            user["group_sessions"] = {}
    return user


async def peek_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Read a user's stored profile without creating one (stats screens must not write)."""
    db = await get_db()
    return await db["users"].find_one({"user_id": int(user_id)})


async def add_user_voice_time(
    user_id: int,
    group_id: int,
    duration_secs: int,
    first_name: str = "",
    username: str = "",
    event_id: Optional[str] = None,
):
    """Record one attendance segment exactly once, then update cached profile totals.

    A unique segment ledger makes participant-leave/recovery replays idempotent. If a
    crash occurs between ledger insertion and profile aggregation, startup reconciliation
    reconstructs the aggregate totals from the ledger.
    """
    duration_secs = max(0, int(duration_secs))
    if duration_secs <= 0:
        return False
    db = await get_db()
    users_col = db["users"]
    users_col_doc = await get_or_create_user(user_id, first_name, username)
    segment_key = str(event_id or uuid.uuid4().hex)
    segment_doc = {
        "_id": segment_key,
        "event_id": segment_key,
        "user_id": int(user_id),
        "group_id": int(group_id),
        "duration_seconds": duration_secs,
        "timestamp": now_ts(),
        "first_name": first_name or users_col_doc.get("first_name", f"User_{user_id}"),
        "username": username or users_col_doc.get("username", ""),
    }
    segments_col = db["voice_time_segments"]
    existing = await segments_col.find_one({"_id": segment_key})
    if existing:
        # Also repair aggregates if an earlier process stopped between ledger insertion
        # and the profile $inc operation.
        await reconcile_user_aggregate(int(user_id))
        await increment_version(_cache_version_key(group_id))
        await increment_version(_cache_version_key(None))
        await archive_event(
            event_id=f"attendance-time:{segment_key}", event_type="participant_duration",
            group_id=int(group_id), event_ts=segment_doc["timestamp"], payload=segment_doc,
        )
        return False
    try:
        await segments_col.insert_one(segment_doc)
    except Exception as exc:
        # The _id uniqueness check handles concurrent/replayed event delivery.
        if "duplicate" in str(exc).lower() or "_id" in str(exc).lower():
            await reconcile_user_aggregate(int(user_id))
            return False
        raise

    group_key = f"group_durations.{int(group_id)}"
    await users_col.update_one(
        {"user_id": int(user_id)},
        {"$inc": {"total_duration": duration_secs, group_key: duration_secs},
         "$set": {"last_active": now_ts(),
                   "first_name": segment_doc["first_name"],
                   "username": segment_doc["username"]}},
        upsert=True,
    )
    await increment_version(_cache_version_key(group_id))
    await increment_version(_cache_version_key(None))
    await archive_event(
        event_id=f"attendance-time:{segment_key}",
        event_type="participant_duration",
        group_id=int(group_id),
        event_ts=segment_doc["timestamp"],
        payload=segment_doc,
    )
    return True


async def reconcile_user_aggregate(user_id: int) -> None:
    """Rebuild lifetime totals from legacy baseline, compacted totals, and live ledger.

    On first migration, subtract existing ledger sums from cached aggregate totals
    before treating the remainder as the pre-ledger baseline. This avoids counting
    the same modern attendance both in the cached aggregate and in the ledger.
    """
    db = await get_db()
    users_col = db["users"]
    user = await users_col.find_one({"user_id": int(user_id)})
    if not user:
        user = await get_or_create_user(user_id)

    segment_cursor = db["voice_time_segments"].find({"user_id": int(user_id)})
    group_sums: Dict[str, int] = {}
    segments_total = 0
    async for segment in segment_cursor:
        duration = max(0, int(segment.get("duration_seconds", 0) or 0))
        segments_total += duration
        key = str(segment.get("group_id"))
        group_sums[key] = group_sums.get(key, 0) + duration

    if not user.get("ledger_migrated_at"):
        # Existing cached values may already include the segment ledger. Subtract
        # ledger totals to avoid doubling; any historical excess remains as baseline.
        cached_total = int(user.get("total_duration", 0) or 0)
        cached_groups = {str(k): int(v or 0) for k, v in (user.get("group_durations", {}) or {}).items()}
        legacy_total = max(0, cached_total - segments_total)
        legacy_groups = {
            key: max(0, value - group_sums.get(key, 0))
            for key, value in cached_groups.items()
        }
        for key, value in group_sums.items():
            legacy_groups.setdefault(key, max(0, cached_groups.get(key, 0) - value))
        await users_col.update_one(
            {"user_id": int(user_id)},
            {"$set": {"legacy_total_duration": legacy_total,
                      "legacy_group_durations": legacy_groups,
                      "ledger_migrated_at": now_ts()}},
        )
        user["legacy_total_duration"] = legacy_total
        user["legacy_group_durations"] = legacy_groups
        user["ledger_migrated_at"] = now_ts()

    legacy_total = int(user.get("legacy_total_duration", 0) or 0)
    legacy_groups = {str(k): int(v or 0) for k, v in (user.get("legacy_group_durations", {}) or {}).items()}
    compacted_total = int(user.get("compacted_total_duration", 0) or 0)
    compacted_groups = {str(k): int(v or 0) for k, v in (user.get("compacted_group_durations", {}) or {}).items()}
    merged_groups = dict(legacy_groups)
    for key, value in compacted_groups.items():
        merged_groups[key] = merged_groups.get(key, 0) + value
    for key, value in group_sums.items():
        merged_groups[key] = merged_groups.get(key, 0) + value
    await users_col.update_one(
        {"user_id": int(user_id)},
        {"$set": {"total_duration": legacy_total + compacted_total + segments_total,
                  "group_durations": merged_groups,
                  "ledger_migrated_at": user.get("ledger_migrated_at", now_ts())}},
        upsert=True,
    )


async def reconcile_user_aggregates() -> int:
    """Startup repair/migration for all user aggregates; returns repaired count."""
    db = await get_db()
    users_cursor = db["users"].find({})
    ids = set()
    async for user in users_cursor:
        if user.get("user_id") is not None:
            ids.add(int(user["user_id"]))
    segments_cursor = db["voice_time_segments"].find({})
    async for segment in segments_cursor:
        if segment.get("user_id") is not None:
            ids.add(int(segment["user_id"]))
    for user_id in sorted(ids):
        await reconcile_user_aggregate(user_id)
    if ids:
        logger.info("Reconciled durable VC-time ledger for %s user(s).", len(ids))
    return len(ids)


async def mark_user_session(user_id: int, group_id: int, session_id: str,
                            first_name: str = "", username: str = "") -> bool:
    """Count a user's attendance once per group-call session (idempotent by session/user)."""
    db = await get_db()
    attendance_col = db["attendance"]
    key = f"{session_id}:{int(user_id)}"
    existing = await attendance_col.find_one({"_id": key})
    if existing:
        return False
    try:
        await attendance_col.insert_one({
            "_id": key, "session_id": str(session_id), "group_id": int(group_id),
            "user_id": int(user_id), "recorded_at": now_ts(),
        })
    except Exception as exc:
        # An already-inserted _id can occur if an update races. Only treat duplicate
        # records as a no-op; other errors should still surface to the caller.
        if "duplicate" not in str(exc).lower() and "_id" not in str(exc).lower():
            raise
        return False
    user = await get_or_create_user(user_id, first_name, username)
    await db["users"].update_one(
        {"user_id": int(user_id)},
        {"$inc": {"sessions_count": 1, f"group_sessions.{int(group_id)}": 1},
         "$set": {"last_active": now_ts()}}, upsert=True,
    )
    await increment_version(_cache_version_key(group_id))
    await increment_version(_cache_version_key(None))
    await archive_event(
        event_id=f"attendance-session:{key}",
        event_type="participant_session_counted",
        group_id=int(group_id),
        event_ts=now_ts(),
        payload={"user_id": int(user_id), "group_id": int(group_id), "session_id": str(session_id),
                 "first_name": first_name or user.get("first_name", f"User_{user_id}")},
    )
    return True


async def get_top_users(group_id: Optional[int] = None, limit: int = 10) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 10000))
    version = await get_version(_cache_version_key(group_id))
    cache_key = f"vcblogger:leaderboard:{'all' if group_id is None else int(group_id)}:{version}:{limit}"
    cached = await cache_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    db = await get_db()
    cursor = db["users"].find({})
    # All-Time leaderboards must not silently truncate the user collection at 10k.
    all_users = await cursor.to_list(None)
    for user in all_users:
        if group_id is not None:
            group_map = user.get("group_durations") or {}
            score = group_map.get(str(group_id), group_map.get(int(group_id), 0))
        else:
            score = user.get("total_duration", 0)
        user["score"] = int(score or 0)
        # Avoid ObjectId serialization issues in Redis.
        if "_id" in user:
            user["_id"] = str(user["_id"])
    all_users = [u for u in all_users if u.get("score", 0) > 0]
    all_users.sort(key=lambda x: x["score"], reverse=True)
    result = all_users[:limit]
    await cache_set_json(cache_key, result, ttl_seconds=15)
    return result


async def count_user_sessions(user_id: int, group_id: Optional[int] = None) -> int:
    """Count distinct session attendance from the attendance ledger when available."""
    db = await get_db()
    query = {"user_id": int(user_id)}
    if group_id is not None:
        query["group_id"] = int(group_id)
    user = await peek_user(user_id) or {}
    if group_id is None:
        aggregate_count = int(user.get("sessions_count", 0) or 0)
    else:
        group_map = user.get("group_sessions") or {}
        aggregate_count = int(group_map.get(str(group_id), group_map.get(group_id, 0)) or 0)
    if aggregate_count > 0:
        return aggregate_count
    # Legacy fallback for records created before aggregate counters existed.
    try:
        return int(await db["attendance"].count_documents(query))
    except Exception as exc:
        logger.debug("Attendance-ledger count unavailable: %s", exc)
        return 0


async def delete_user_data(user_id: int) -> None:
    """Delete a user's profile and attendance records from primary and optional archive storage."""
    db = await get_db()
    uid = int(user_id)
    for name in ("voice_time_segments", "attendance"):
        await db[name].delete_many({"user_id": uid})
    await db["users"].delete_many({"user_id": uid})
    # Scan session snapshots in Python so this also works on lightweight document adapters.
    async for row in db["sessions"].find({}):
        participants = row.get("participants") or []
        historical = row.get("historical_participants") or []
        updates = {}
        if any(int(person.get("user_id", 0) or 0) == uid for person in participants):
            updates["participants"] = [person for person in participants if int(person.get("user_id", 0) or 0) != uid]
        if any(int(person.get("user_id", 0) or 0) == uid for person in historical):
            updates["historical_participants"] = [person for person in historical if int(person.get("user_id", 0) or 0) != uid]
        if updates:
            await db["sessions"].update_one({"session_id": row.get("session_id")}, {"$set": updates})
    await db["blocked_users"].delete_many({"user_id": uid})
    await increment_version(_cache_version_key(None))
    try:
        from .postgres_archive import delete_user_archive_data, is_configured
        if is_configured():
            await delete_user_archive_data(uid)
    except Exception as exc:
        logger.warning("Could not fully clear optional archive data for user %s: %s", uid, exc)
