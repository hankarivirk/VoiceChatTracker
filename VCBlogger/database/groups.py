"""Group settings and preferences storage."""
from typing import Dict, Any
from .mongo import get_db
from ..config import MIN_DURATION_THRESHOLD


async def get_group_settings(group_id: int) -> Dict[str, Any]:
    """Retrieve group configuration or defaults."""
    db = await get_db()
    groups_col = db["groups"]
    defaults = {
        "group_id": group_id,
        "title": "Unnamed group",
        "logging_enabled": True,
        "min_duration": MIN_DURATION_THRESHOLD,
        "blacklisted_users": [],
        "notify_on_end": True,
        "notify_on_join": True,
        "notify_on_leave": True,
        "event_message_ttl_seconds": 10,
    }
    doc = await groups_col.find_one({"group_id": group_id})
    # Reading never creates a row: only real changes (title, settings) are stored.
    # Partial documents are completed with defaults.
    return {**defaults, **(doc or {})}


async def update_group_settings(group_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update settings for a group."""
    db = await get_db()
    groups_col = db["groups"]
    await groups_col.update_one({"group_id": group_id}, {"$set": updates}, upsert=True)
    return await get_group_settings(group_id)


async def is_group_logging_enabled(group_id: int) -> bool:
    """Check if group has active logging enabled."""
    cfg = await get_group_settings(group_id)
    return cfg.get("logging_enabled", True)
