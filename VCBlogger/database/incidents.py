"""Voice chat anomaly & incident tracking."""
import uuid
from typing import Dict, Any, List
from .mongo import get_db
from .postgres_archive import archive_event
from ..utils.time_utils import now_ts


async def log_incident(group_id: int, incident_type: str, details: str, severity: str = "warning") -> str:
    """Record a voice chat anomaly event."""
    db = await get_db()
    col = db["incidents"]
    inc_id = str(uuid.uuid4())
    doc = {
        "incident_id": inc_id,
        "group_id": group_id,
        "type": incident_type,
        "details": details,
        "severity": severity,
        "timestamp": now_ts(),
        "resolved": False,
    }
    await col.insert_one(doc)
    await archive_event(
        event_id=f"incident:{inc_id}",
        event_type="incident",
        group_id=int(group_id),
        event_ts=doc["timestamp"],
        payload=doc,
    )
    return inc_id


async def get_recent_incidents(group_id: int = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Fetch recent incident records."""
    db = await get_db()
    col = db["incidents"]
    q = {}
    if group_id:
        q["group_id"] = group_id
    cursor = col.find(q).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(limit)


async def resolve_incident(incident_id: str):
    """Mark incident as resolved."""
    db = await get_db()
    col = db["incidents"]
    await col.update_one({"incident_id": incident_id}, {"$set": {"resolved": True}})
