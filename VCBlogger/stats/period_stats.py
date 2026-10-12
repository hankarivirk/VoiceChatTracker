"""Today / Weekly / Monthly / All-time analytics for one group or for every group.

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

PERIODS = ("today", "weekly", "monthly", "all")

PERIOD_ALIASES = {
    "today": "today", "day": "today", "daily": "today",
    "week": "weekly", "weekly": "weekly", "thisweek": "weekly",
    "month": "monthly", "monthly": "monthly", "thismonth": "monthly",
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
    return {"today": "Today", "weekly": "This week", "monthly": "This month", "all": "All time"}[period]


def period_bounds(period: str, reference_ts: Optional[int] = None) -> Optional[Tuple[int, int]]:
    """[start, end) Unix bounds of the local calendar period; None for all-time."""
    if period == "all":
        return None
    try:
        tz = ZoneInfo(STATS_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    timestamp = now_ts() if reference_ts is None else reference_ts
    now = datetime.fromtimestamp(timestamp, tz=tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        start = start - timedelta(days=start.weekday())
        end = start + timedelta(days=7)
    elif period == "today":
        end = start + timedelta(days=1)
    elif period == "monthly":
        start = start.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    else:
        raise ValueError(f"Unsupported period: {period}")
    return int(start.timestamp()), int(end.timestamp())



def _presence_intervals(session: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Extract reliable participant-presence intervals from a session document."""
    intervals: List[Tuple[float, float]] = []
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
    return intervals


def _union_seconds(intervals: List[Tuple[float, float]], bounds: Optional[Tuple[int, int]] = None) -> int:
    """Return the union length, optionally clipped to [period_start, period_end)."""
    if bounds:
        lo, hi = bounds
        intervals = [(max(start, lo), min(end, hi)) for start, end in intervals]
        intervals = [(start, end) for start, end in intervals if end > start]
    if not intervals:
        return 0
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


def _occupied_duration(session: Dict[str, Any], bounds: Optional[Tuple[int, int]] = None) -> int:
    """Count only periods where at least one participant was present.

    Presence timestamps are the source of truth. If old records lack timestamps,
    never substitute raw VC uptime: that would incorrectly count empty voice chats.
    A stored occupied_seconds value is used only when the entire session falls
    inside the requested period, or for all-time totals where no clipping is needed.
    """
    occupied = session.get("occupied_seconds")
    trusted_counter = session.get("duration_type") == "occupied_presence"
    # Only trust the aggregate for sessions explicitly written by presence-based
    # tracking. Older versions stored VC uptime/open-mic duration in this field.
    if not bounds and occupied is not None and trusted_counter:
        return max(0, int(occupied or 0))

    intervals = _presence_intervals(session)
    if intervals:
        return _union_seconds(intervals, bounds)

    if occupied is None or not trusted_counter:
        return 0
    occupied = max(0, int(occupied or 0))
    if not bounds:
        return occupied
    try:
        start = float(session.get("start_time", 0) or 0)
        end = float(session.get("end_time", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if start >= bounds[0] and end <= bounds[1] and end >= start:
        return occupied
    return 0


def _active_session_document(session: Any) -> Dict[str, Any]:
    """Build interval data for an active in-memory call without counting empty time."""
    people = list(session.historical_participants.values())
    now = now_ts()
    for participant in session.participants.values():
        person = participant.to_dict()
        person["left_at"] = now
        people.append(person)
    return {
        "start_time": session.started_at,
        "end_time": now,
        "occupied_seconds": session.current_duration(),
        "duration_type": "occupied_presence",
        "duration": session.current_duration(),
        "participants": people,
        "peak_participants": session.peak_participants,
        "is_active": True,
    }

async def _period_ledger_rows(db: Any, filters: Dict[str, Any], bounds: Tuple[int, int]):
    """Yield ledger rows overlapping a period, with a fallback for pre-interval rows."""
    start, end = bounds
    seen = set()
    overlap_query = dict(filters)
    overlap_query.update({"start_time": {"$lt": end}, "end_time": {"$gt": start}})
    async for row in db["voice_time_segments"].find(overlap_query):
        key = row.get("_id") or (row.get("user_id"), row.get("group_id"), row.get("timestamp"), row.get("duration_seconds"))
        seen.add(key)
        yield row

    # Old ledger records only have a timestamp (written when the user left).
    legacy_query = dict(filters)
    legacy_query["timestamp"] = {"$gte": start, "$lt": end}
    async for row in db["voice_time_segments"].find(legacy_query):
        key = row.get("_id") or (row.get("user_id"), row.get("group_id"), row.get("timestamp"), row.get("duration_seconds"))
        if key in seen:
            continue
        seen.add(key)
        yield row


def _ledger_seconds_for_period(row: Dict[str, Any], bounds: Tuple[int, int]) -> int:
    """Clip interval-aware ledger rows; legacy rows use their leave timestamp."""
    try:
        start = row.get("start_time")
        end = row.get("end_time")
        if start is not None and end is not None:
            return max(0, int(min(float(end), bounds[1]) - max(float(start), bounds[0])))
        timestamp = int(row.get("timestamp", 0) or 0)
        if bounds[0] <= timestamp < bounds[1]:
            return max(0, int(row.get("duration_seconds", 0) or 0))
    except (TypeError, ValueError):
        return 0
    return 0


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
        # Pull a broad cached base, then overlay current visits so the all-time
        # ranking changes live before participant-leave events write the ledger.
        base = await get_top_users(group_id=group_id, limit=10000)
        totals = {int(row.get("user_id", 0) or 0): dict(row) for row in base if int(row.get("user_id", 0) or 0) > 0}
        try:
            from ..vc.sessions import session_manager
            for gid, active in list(session_manager.sessions.items()):
                if group_id is not None and int(gid) != int(group_id):
                    continue
                for participant in active.participants.values():
                    uid = int(participant.user_id)
                    if uid in getattr(active, "suppressed_user_ids", set()):
                        continue
                    seconds = max(0, int(participant.duration()))
                    if seconds <= 0:
                        continue
                    item = totals.setdefault(uid, {
                        "user_id": uid, "first_name": participant.first_name,
                        "username": participant.username, "score": 0, "total_duration": 0,
                    })
                    item["score"] = max(0, int(item.get("score", item.get("total_duration", 0)) or 0)) + seconds
                    item["total_duration"] = item["score"]
                    item["first_name"] = participant.first_name or item.get("first_name", "")
                    item["username"] = participant.username or item.get("username", "")
        except Exception:
            pass
        result = [row for row in totals.values() if int(row.get("score", row.get("total_duration", 0)) or 0) > 0]
        result.sort(key=lambda row: (-int(row.get("score", row.get("total_duration", 0)) or 0), int(row.get("user_id", 0) or 0)))
        return result[:max(1, min(int(limit), 10000))]
    bounds = period_bounds(period)
    query: Dict[str, Any] = {}
    if group_id is not None:
        query["group_id"] = int(group_id)
    db = await get_db()
    totals: Dict[int, Dict[str, Any]] = {}
    async for row in _period_ledger_rows(db, query, bounds):
        uid = int(row.get("user_id", 0) or 0)
        if not uid:
            continue
        item = totals.setdefault(uid, {
            "user_id": uid, "first_name": "", "username": "", "score": 0, "total_duration": 0,
        })
        item["score"] += _ledger_seconds_for_period(row, bounds)
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
            now = now_ts()
            for participant in session.participants.values():
                if int(participant.user_id) in getattr(session, "suppressed_user_ids", set()):
                    continue
                if bounds:
                    # Clip an ongoing visit to the selected period, including a visit
                    # that began before midnight or before the current week.
                    start = max(float(participant.joined_at), float(bounds[0]))
                    end = min(float(now), float(bounds[1]))
                    live_seconds = max(0, int(end - start))
                else:
                    live_seconds = max(0, int(participant.duration()))
                if live_seconds <= 0:
                    continue
                uid = int(participant.user_id)
                item = totals.setdefault(uid, {"user_id": uid, "first_name": participant.first_name, "username": participant.username, "score": 0, "total_duration": 0})
                item["score"] += live_seconds
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
        data = await get_user_stats(user_id, group_id)
        # The active visit is not in the ledger until the user leaves; include it
        # in the all-time card so it updates while the user remains in VC.
        try:
            from ..vc.sessions import session_manager
            live_seconds = 0
            live_calls = 0
            for gid, active in list(session_manager.sessions.items()):
                if group_id is not None and int(gid) != int(group_id):
                    continue
                participant = active.participants.get(int(user_id))
                if participant is None or int(user_id) in getattr(active, "suppressed_user_ids", set()):
                    continue
                live_seconds += max(0, int(participant.duration()))
                live_calls += 1
            if live_seconds:
                data["total_duration"] += live_seconds
                data["formatted_duration"] = format_duration(data["total_duration"])
                data["sessions_count"] += live_calls
                average = int(data["total_duration"] / data["sessions_count"]) if data["sessions_count"] else 0
                data["average_session_seconds"] = average
                data["formatted_average"] = format_duration(average)
                data["tier"] = compute_user_tier(data["total_duration"])
        except Exception:
            pass
        return data
    bounds = period_bounds(period)
    db = await get_db()
    query: Dict[str, Any] = {"user_id": int(user_id)}
    if group_id is not None:
        query["group_id"] = int(group_id)
    total = 0
    async for row in _period_ledger_rows(db, query, bounds):
        total += _ledger_seconds_for_period(row, bounds)
    # Active visits have not been written to the durable ledger yet. Include only
    # the overlap with this period so My Stats refreshes while a user is still in VC.
    try:
        from ..vc.sessions import session_manager
        now = now_ts()
        for gid, active in list(session_manager.sessions.items()):
            if group_id is not None and int(gid) != int(group_id):
                continue
            participant = active.participants.get(int(user_id))
            if participant is None or int(user_id) in getattr(active, "suppressed_user_ids", set()):
                continue
            start = max(float(participant.joined_at), float(bounds[0]))
            end = min(float(now), float(bounds[1]))
            total += max(0, int(end - start))
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
    bounds = period_bounds(period)
    async for session in db["sessions"].find(_session_query(group_id, period)):
        total_calls += 1
        call_seconds += _occupied_duration(session, bounds)
        peak = max(peak, int(session.get("peak_participants", 0) or 0))

    # Include active calls in the visible stats so a running VC is not invisible
    # until the call ends. Its time is still based on participant presence only.
    bounds = period_bounds(period)
    try:
        from ..vc.sessions import session_manager
        now = now_ts()
        for gid, active in list(session_manager.sessions.items()):
            if group_id is not None and int(gid) != int(group_id):
                continue
            active_seconds = _occupied_duration(_active_session_document(active), bounds)
            if active_seconds > 0:
                total_calls += 1
                call_seconds += active_seconds
                peak = max(peak, int(active.peak_participants))
    except Exception:
        pass

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
        ledger: Dict[str, Any] = {}
        if group_id is not None:
            ledger["group_id"] = int(group_id)
        async for row in _period_ledger_rows(db, ledger, bounds):
            member_seconds += _ledger_seconds_for_period(row, bounds)
            try:
                uid = int(row.get("user_id", 0) or 0)
            except (TypeError, ValueError):
                uid = 0
            if uid:
                members.add(uid)

    # Active participant durations are not in the durable ledger until leave.
    try:
        from ..vc.sessions import session_manager
        now = now_ts()
        bounds = period_bounds(period)
        for gid, active in list(session_manager.sessions.items()):
            if group_id is not None and int(gid) != int(group_id):
                continue
            for participant in active.participants.values():
                uid = int(participant.user_id)
                if uid in getattr(active, "suppressed_user_ids", set()):
                    continue
                if bounds:
                    seconds = max(0, int(min(now, bounds[1]) - max(float(participant.joined_at), float(bounds[0]))))
                else:
                    seconds = max(0, int(participant.duration()))
                if seconds > 0:
                    member_seconds += seconds
                    members.add(uid)
    except Exception:
        pass

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
    """Rank groups by occupied VC time, clipped to the selected period."""
    db = await get_db()
    bounds = period_bounds(period)
    groups: Dict[int, Dict[str, Any]] = {}

    # Use an overlap query rather than scanning the entire session collection on
    # each 15-second refresh. Interval clipping still handles overnight/week-boundary calls.
    session_query: Dict[str, Any] = {"is_active": False}
    if bounds:
        session_query.update({
            "start_time": {"$lt": bounds[1]},
            "end_time": {"$gte": bounds[0]},
        })
    async for session in db["sessions"].find(session_query):
        gid = int(session.get("group_id", 0) or 0)
        if not gid:
            continue
        seconds = _occupied_duration(session, bounds)
        if seconds <= 0:
            continue
        item = groups.setdefault(gid, {"group_id": gid, "sessions": 0, "vc_seconds": 0, "peak": 0})
        item["sessions"] += 1
        item["vc_seconds"] += seconds
        item["peak"] = max(item["peak"], int(session.get("peak_participants", 0) or 0))

    # Include live calls from the exact intervals, not VC uptime or the session's
    # start date. This also lets an active overnight call contribute to Today.
    try:
        from ..vc.sessions import session_manager
        for gid, active in list(session_manager.sessions.items()):
            seconds = _occupied_duration(_active_session_document(active), bounds)
            if seconds <= 0:
                continue
            item = groups.setdefault(int(gid), {"group_id": int(gid), "sessions": 0, "vc_seconds": 0, "peak": 0})
            item["vc_seconds"] += seconds
            item["sessions"] += 1
            item["peak"] = max(item["peak"], int(active.peak_participants))
            item["active"] = True
    except Exception:
        # A stats screen must still render if in-memory tracking is unavailable.
        pass

    result = [item for item in groups.values() if int(item.get("vc_seconds", 0) or 0) > 0]
    result.sort(key=lambda x: (-x["vc_seconds"], x["group_id"]))
    result = result[:max(1, min(int(limit), 100))]
    top_ids = {int(item["group_id"]) for item in result}
    titles: Dict[int, str] = {}
    if top_ids:
        async for row in db["groups"].find({"group_id": {"$in": list(top_ids)}}):
            try:
                titles[int(row.get("group_id"))] = str(row.get("title") or "")
            except (TypeError, ValueError):
                continue
    for item in result:
        gid = int(item["group_id"])
        item["title"] = titles.get(gid, "")
        item["formatted_vc_time"] = format_duration(item["vc_seconds"])
    return result

