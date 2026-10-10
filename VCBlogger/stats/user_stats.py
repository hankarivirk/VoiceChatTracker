"""User level analytics and personal stats calculator."""
from typing import Dict, Any, Optional
from ..database.users import peek_user, count_user_sessions
from ..utils.formatting import format_duration
from ..utils.time_utils import now_ts


def compute_user_tier(duration_secs: int) -> str:
    """Assign tier badge based on total VC time."""
    hours = duration_secs / 3600
    if hours >= 100:
        return "👑 Legend"
    if hours >= 50:
        return "💎 Diamond"
    if hours >= 20:
        return "🥇 Gold"
    if hours >= 5:
        return "🥈 Silver"
    return "🥉 Bronze"


async def get_user_stats(user_id: int, group_id: Optional[int] = None) -> Dict[str, Any]:
    """Calculate detailed voice statistics for a user."""
    user = await peek_user(user_id) or {}
    total_sec = int(user.get("total_duration", 0) or 0)
    if group_id is not None:
        group_map = user.get("group_durations", {}) or {}
        total_sec = int(group_map.get(str(group_id), group_map.get(int(group_id), 0)) or 0)

    try:
        from ..vc.sessions import session_manager
        for gid, session in list(session_manager.sessions.items()):
            if group_id is not None and int(gid) != int(group_id):
                continue
            if int(user_id) in getattr(session, "suppressed_user_ids", set()):
                continue
            participant = session.participants.get(int(user_id))
            if participant is not None and participant.record_time:
                total_sec += int(participant.duration())
    except Exception:
        pass
    sessions_count = await count_user_sessions(user_id, group_id)
    avg_sec = int(total_sec / max(1, sessions_count)) if sessions_count else 0

    return {
        "user_id": user_id,
        "first_name": user.get("first_name") or f"User_{user_id}",
        "username": user.get("username", ""),
        "total_duration": total_sec,
        "formatted_duration": format_duration(total_sec),
        "sessions_count": sessions_count,
        "average_session_seconds": avg_sec,
        "formatted_average": format_duration(avg_sec),
        "tier": compute_user_tier(total_sec),
        "last_active": user.get("last_active") or now_ts(),
    }
