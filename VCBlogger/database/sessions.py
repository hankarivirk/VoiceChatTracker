"""Voice chat session record persistence in MongoDB with optional Neon archive."""
import uuid
from typing import Dict, Any, List, Optional
from .mongo import get_db
from .postgres_archive import archive_event
from ..utils.time_utils import now_ts


async def create_session(group_id: int, initial_participants: List[Dict[str, Any]] = None) -> str:
    """Create a new active VC session record."""
    db = await get_db()
    sessions_col = db["sessions"]
    session_id = str(uuid.uuid4())
    start = now_ts()
    doc = {
        "session_id": session_id,
        "group_id": int(group_id),
        "start_time": start,
        "end_time": None,
        "duration": 0,
        "is_active": True,
        "peak_participants": len(initial_participants or []),
        "participants": initial_participants or [],
        "last_snapshot": start,
    }
    await sessions_col.insert_one(doc)
    await archive_event(
        event_id=f"session:{session_id}:started",
        event_type="session_started",
        group_id=group_id,
        event_ts=start,
        payload={"session_id": session_id, "group_id": group_id, "start_time": start},
    )
    return session_id


async def save_session_snapshot(session_id: str, updates: Dict[str, Any]):
    """Update ongoing session state in the primary database."""
    db = await get_db()
    await db["sessions"].update_one({"session_id": session_id}, {"$set": updates})


async def close_session(session_id: str, duration: int, participants: List[Dict[str, Any]], peak: int):
    """Mark a session completed and mirror a full snapshot to optional Neon archive."""
    db = await get_db()
    sessions_col = db["sessions"]
    end = now_ts()
    await sessions_col.update_one(
        {"session_id": session_id},
        {"$set": {
            "end_time": end,
            "duration": max(0, int(duration)),
            "occupied_seconds": max(0, int(duration)),
            "is_active": False,
            "participants": participants,
            "peak_participants": int(peak),
        }}
    )
    completed = await sessions_col.find_one({"session_id": session_id}) or {
        "session_id": session_id,
        "end_time": end,
        "duration": max(0, int(duration)),
        "participants": participants,
        "peak_participants": int(peak),
    }
    await archive_event(
        event_id=f"session:{session_id}:completed",
        event_type="session_completed",
        group_id=int(completed.get("group_id", 0)),
        event_ts=end,
        payload=completed,
    )


async def get_active_sessions(group_id: Optional[int] = None) -> List[Dict[str, Any]]:
    db = await get_db()
    query = {"is_active": True}
    if group_id is not None:
        query["group_id"] = int(group_id)
    cursor = db["sessions"].find(query).sort("start_time", -1)
    return await cursor.to_list(1000)


async def get_recent_sessions(group_id: Optional[int] = None, limit: int = 15) -> List[Dict[str, Any]]:
    """Return completed sessions only, latest first."""
    db = await get_db()
    query = {"is_active": False}
    if group_id is not None:
        query["group_id"] = int(group_id)
    cursor = db["sessions"].find(query).sort("start_time", -1).limit(max(1, min(int(limit), 1000)))
    return await cursor.to_list(max(1, min(int(limit), 1000)))
