"""Today / Weekly / All-time analytics for one group or for every group.

Rules that keep numbers consistent across screens:
* Voice time of a period comes from the timestamped voice_time_segments ledger.
* A call belongs to the period in which it ENDED (the same moment its voice-time
  segments are written), so calls, call time and member time always agree.
* All-time totals come from the lifetime user aggregates (legacy history included).
* Reading statistics never creates or modifies any record.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional, Dict, Any, List, Tuple

from ..config import STATS_TIMEZONE
from ..database.mongo import get_db
from ..database.users import peek_user, get_top_users
from ..utils.formatting import format_duration
from ..utils.time_utils import now_ts

PERIODS = ("today", "weekly", "all")

PERIOD_ALIASES = {
    "today": "today", "day": "today", "daily": "today",
    "week": "weekly", "weekly": "weekly", "thisweek": "weekly",
    "all": "all", "alltime": "all", "lifetime": "all",
}


def parse_period(token: str) -> Optional[str]:
    """Return the period for one word, or None when the word is not a period."""
    return PERIOD_ALIASES.get((token or "").strip().lower().replace("-", "").replace("_", ""))


def normalize_period(value: Optional[str]) -> str:
    """'' -> all-time; unknown words -> '' (caller shows usage)."""
    if not value:
        return "all"
    return parse_period(value) or ""


def period_label(period: str) -> str:
    return {"today": "Today", "weekly": "This week", "all": "All time"}[period]


def period_bounds(period: str, reference_ts: Optional[int] = None) -> Optional[Tuple[int, int]]:
    """[start, end) Unix bounds of the local calendar period; None for all-time."""
    if period == "all":
        return None
    try:
        tz = ZoneInfo(STATS_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    now = datetime.fromtimestamp(reference_ts or now_ts(), tz=tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        start = start - timedelta(days=start.weekday())
        end = start + timedelta(days=7)
    elif period == "today":
        end = start + timedelta(days=1)
    else:
        raise ValueError(f"Unsupported period: {period}")
    return int(start.timestamp()), int(end.timestamp())



def _occupied_duration(session: Dict[str, Any]) -> int:
    """Union participant-presence intervals when history has timestamps.

    This repairs legacy group rankings where VC uptime was stored as group time.
    If a legacy record lacks participant timestamps, use its stored duration as a
    documented fallback because true occupied time cannot be reconstructed.
    """
    intervals = []
    for person in (session.get("participants") or []):
        segments = person.get("segments") or []
        if not segments and person.get("joined_at") and person.get("left_at"):
            segments = [person]
        for segment in segments:
            try:
                start = float(segment.get("joined_at", 0) or 0)
                end = float(segment.get("left_at", 0) or 0)
                if end > start > 0:
                    intervals.append((start, end))
            except (TypeError, ValueError):
                continue
    if not intervals:
        return max(0, int(session.get("occupied_seconds", session.get("duration", 0)) or 0))
    intervals.sort()
    total = 0.0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    total += end - start
    return max(0, int(total))

def _session_query(group_id: Optional[int], period: str) -> Dict[str, Any]:
    query: Dict[str, Any] = {"is_active": False}
    if group_id is not None:
        query["group_id"] = int(group_id)
    bounds = period_bounds(period)
    if bounds:
        query["end_time"] = {"$gte": bounds[0], "$lt": bounds[1]}
    return query


async def get_period_user_totals(group_id: Optional[int], period: str, limit: int = 10000) -> List[Dict[str, Any]]:
    """Users sorted by recorded voice time for a group (or all groups)."""
    if period == "all":
        base_users = await get_top_users(group_id=group_id, limit=limit)
        try:
            from ..vc.sessions import session_manager
            totals = {int(u["user_id"]): dict(u) for u in base_users}
            has_active = False
            for gid, session in list(session_manager.sessions.items()):
                if group_id is not None and int(gid) != int(group_id):
                    continue
                for participant in session.participants.values():
                    if int(participant.user_id) in getattr(session, "suppressed_user_ids", set()):
                        continue
                    if not participant.record_time:
                        continue
                    uid = int(participant.user_id)
                    item = totals.setdefault(uid, {
                        "user_id": uid, "first_name": participant.first_name, "username": participant.username,
                        "score": 0, "total_duration": 0,
                    })
                    item["score"] += max(0, int(participant.duration()))
                    item["total_duration"] = item["score"]
                    item["first_name"] = participant.first_name or item.get("first_name", "")
                    item["username"] = participant.username or item.get("username", "")
                    has_active = True
            if has_active:
                res = [v for v in totals.values() if v.get("score", 0) > 0]
                res.sort(key=lambda x: (-x["score"], int(x["user_id"])))
                return res[:max(1, min(int(limit), 10000))]
        except Exception:
            pass
        return base_users
    bounds = period_bounds(period)
    query: Dict[str, Any] = {"timestamp": {"$gte": bounds[0], "$lt": bounds[1]}}
    if group_id is not None:
        query["group_id"] = int(group_id)
    db = await get_db()
    totals: Dict[int, Dict[str, Any]] = {}
    async for row in db["voice_time_segments"].find(query):
        uid = int(row.get("user_id", 0) or 0)
        if not uid:
            continue
        item = totals.setdefault(uid, {
            "user_id": uid, "first_name": "", "username": "", "score": 0, "total_duration": 0,
        })
        item["score"] += max(0, int(row.get("duration_seconds", 0) or 0))
        item["total_duration"] = item["score"]
        if row.get("first_name"):
            item["first_name"] = row["first_name"]
        if row.get("username"):
            item["username"] = row["username"]
    # Include currently-present participants so the leaderboard changes live, not only after leave.
    try:
        from ..vc.sessions import session_manager
        bounds = period_bounds(period)
        for gid, session in list(session_manager.sessions.items()):
            if group_id is not None and int(gid) != int(group_id):
                continue
            for participant in session.participants.values():
                if int(participant.user_id) in getattr(session, "suppressed_user_ids", set()):
                    continue
                if bounds and not (bounds[0] <= int(participant.joined_at) < bounds[1]):
                    continue
                uid = int(participant.user_id)
                item = totals.setdefault(uid, {"user_id": uid, "first_name": participant.first_name, "username": participant.username, "score": 0, "total_duration": 0})
                item["score"] += max(0, int(participant.duration()))
                item["total_duration"] = item["score"]
                item["first_name"] = participant.first_name or item["first_name"]
                item["username"] = participant.username or item["username"]
    except Exception:
        pass
    result = [v for v in totals.values() if v["score"] > 0]
    result.sort(key=lambda x: (-x["score"], int(x["user_id"])))
    return result[:max(1, min(int(limit), 10000))]


async def get_user_rank(user_id: int, group_id: Optional[int], period: str) -> Tuple[Optional[int], int]:
    """(rank, ranked_users) for one person; rank is None when they have no time."""
    users = await get_period_user_totals(group_id, period, limit=10000)
    for rank, row in enumerate(users, 1):
        if int(row.get("user_id", 0) or 0) == int(user_id):
            return rank, len(users)
    return None, len(users)


async def get_period_user_stats(user_id: int, group_id: Optional[int], period: str) -> Dict[str, Any]:
    """One person's voice time, calls and average. Read-only."""
    from .user_stats import compute_user_tier, get_user_stats
    if period == "all":
        return await get_user_stats(user_id, group_id)
    bounds = period_bounds(period)
    db = await get_db()
    query: Dict[str, Any] = {"user_id": int(user_id), "timestamp": {"$gte": bounds[0], "$lt": bounds[1]}}
    if group_id is not None:
        query["group_id"] = int(group_id)
    total = 0
    async for row in db["voice_time_segments"].find(query):
        total += max(0, int(row.get("duration_seconds", 0) or 0))
    try:
        from ..vc.sessions import session_manager
        for gid, session in list(session_manager.sessions.items()):
            if group_id is not None and int(gid) != int(group_id):
                continue
            if int(user_id) in getattr(session, "suppressed_user_ids", set()):
                continue
            participant = session.participants.get(int(user_id))
            if participant is not None and participant.record_time:
                dur = int(participant.duration())
                if bounds and participant.joined_at < bounds[0]:
                    dur = max(0, min(dur, now_ts() - bounds[0]))
                total += dur
    except Exception:
        pass
    attendance: Dict[str, Any] = {"user_id": int(user_id), "recorded_at": {"$gte": bounds[0], "$lt": bounds[1]}}
    if group_id is not None:
        attendance["group_id"] = int(group_id)
    try:
        calls = int(await db["attendance"].count_documents(attendance))
    except Exception:
        calls = 0
    average = int(total / calls) if calls else 0
    user = await peek_user(user_id) or {}
    return {
        "user_id": int(user_id),
        "first_name": user.get("first_name") or f"User_{user_id}",
        "username": user.get("username", ""),
        "total_duration": total,
        "formatted_duration": format_duration(total),
        "sessions_count": calls,
        "average_session_seconds": average,
        "formatted_average": format_duration(average),
        "tier": compute_user_tier(total),
        "period": period,
    }


async def get_period_group_summary(group_id: Optional[int], period: str) -> Dict[str, Any]:
    """Calls, call time, member time and peak for one group or all groups."""
    db = await get_db()
    total_calls = 0
    call_seconds = 0
    peak = 0
    async for session in db["sessions"].find(_session_query(group_id, period)):
        total_calls += 1
        call_seconds += _occupied_duration(session)
        peak = max(peak, int(session.get("peak_participants", 0) or 0))

    member_seconds = 0
    members = set()
    if period == "all":
        async for user in db["users"].find({}):
            try:
                uid = int(user.get("user_id", 0) or 0)
            except (TypeError, ValueError):
                continue
            if group_id is None:
                seconds = int(user.get("total_duration", 0) or 0)
            else:
                group_map = user.get("group_durations", {}) or {}
                seconds = int(group_map.get(str(group_id), group_map.get(int(group_id), 0)) or 0)
            if seconds > 0 and uid:
                member_seconds += seconds
                members.add(uid)
    else:
        bounds = period_bounds(period)
        ledger: Dict[str, Any] = {"timestamp": {"$gte": bounds[0], "$lt": bounds[1]}}
        if group_id is not None:
            ledger["group_id"] = int(group_id)
        async for row in db["voice_time_segments"].find(ledger):
            member_seconds += max(0, int(row.get("duration_seconds", 0) or 0))
            try:
                uid = int(row.get("user_id", 0) or 0)
            except (TypeError, ValueError):
                uid = 0
            if uid:
                members.add(uid)

    average = int(call_seconds / total_calls) if total_calls else 0
    return {
        "group_id": group_id,
        "total_sessions": total_calls,
        "total_duration_seconds": call_seconds,
        "formatted_total_duration": format_duration(call_seconds),
        "participant_duration_seconds": member_seconds,
        "formatted_participant_duration": format_duration(member_seconds),
        "peak_participants": peak,
        "average_session_seconds": average,
        "formatted_average_session": format_duration(average),
        "unique_users_tracked": len(members),
    }


async def get_global_group_breakdown(period: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Groups ranked by occupied VC time (only while at least one user is present)."""
    db = await get_db()
    groups: Dict[int, Dict[str, Any]] = {}
    async for session in db["sessions"].find(_session_query(None, period)):
        gid = int(session.get("group_id", 0) or 0)
        if not gid:
            continue
        item = groups.setdefault(gid, {"group_id": gid, "sessions": 0, "vc_seconds": 0, "peak": 0})
        item["sessions"] += 1
        item["vc_seconds"] += _occupied_duration(session)
        item["peak"] = max(item["peak"], int(session.get("peak_participants", 0) or 0))
    titles: Dict[int, str] = {}
    async for row in db["groups"].find({}):
        try:
            titles[int(row.get("group_id"))] = str(row.get("title") or "")
        except (TypeError, ValueError):
            continue
    # Include active calls using occupied time only; empty-call uptime is never ranked.
    try:
        from ..vc.sessions import session_manager
        bounds = period_bounds(period)
        for gid, session in list(session_manager.sessions.items()):
            if bounds and int(session.started_at) >= bounds[1]:
                continue
            seconds = max(0, int(session.current_duration()))
            if bounds and int(session.started_at) < bounds[0]:
                seconds = max(0, min(seconds, now_ts() - bounds[0]))
            if seconds <= 0:
                continue
            item = groups.setdefault(int(gid), {"group_id": int(gid), "sessions": 0, "vc_seconds": 0, "peak": 0})
            item["vc_seconds"] += seconds
            item["peak"] = max(item["peak"], int(session.peak_participants))
            item["active"] = True
    except Exception:
        pass
    result = []
    for gid, item in groups.items():
        if int(item.get("vc_seconds", 0) or 0) <= 0:
            continue
        item["title"] = titles.get(gid, "")
        item["formatted_vc_time"] = format_duration(item["vc_seconds"])
        result.append(item)
    result.sort(key=lambda x: (-x["vc_seconds"], x["group_id"]))
    return result[:max(1, min(int(limit), 100))]
