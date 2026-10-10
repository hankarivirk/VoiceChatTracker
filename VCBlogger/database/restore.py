"""Merge a VCBlogger gzip JSON backup into the configured primary database.

Usage: python -m VCBlogger.database.restore /path/to/vcblogger-backup.json.gz
This is a merge/upsert restore; it does not delete records absent from the backup.
"""
import asyncio
import gzip
import json
import sys
from pathlib import Path

from .mongo import get_db, close
from ..utils.logging import logger

_PRIMARY = {"groups": "group_id", "users": "user_id", "sessions": "session_id", "incidents": "incident_id", "blocked_users": "user_id"}
_COLLECTIONS = ("groups", "users", "sessions", "incidents", "attendance", "voice_time_segments", "maintenance_state", "blocked_users")

async def restore(path: Path) -> int:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    collections = payload.get("collections")
    if not isinstance(collections, dict):
        raise ValueError("Backup has no valid collections object")
    db = await get_db()
    count = 0
    for name in _COLLECTIONS:
        docs = collections.get(name, [])
        if not isinstance(docs, list):
            raise ValueError(f"Invalid backup collection: {name}")
        for source in docs:
            if not isinstance(source, dict):
                continue
            doc = dict(source)
            key = _PRIMARY.get(name)
            if key and doc.get(key) is not None:
                query = {key: doc[key]}
            elif doc.get("_id") is not None:
                query = {"_id": doc["_id"]}
            else:
                raise ValueError(f"Cannot safely identify a document in {name}")
            # MongoDB does not allow _id to be changed in $set; primary-key queries
            # preserve identity and the backup is merged into the destination.
            doc.pop("_id", None)
            await db[name].update_one(query, {"$set": doc}, upsert=True)
            count += 1
    logger.warning("Merged %s backup documents from %s. Review stats and health before resuming normal use.", count, path)
    return count

async def _main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m VCBlogger.database.restore /path/to/vcblogger-backup.json.gz")
    try:
        count = await restore(Path(sys.argv[1]).expanduser())
        print(f"Merged {count} documents. This was a non-destructive merge; it did not delete destination-only records.")
    finally:
        await close()

if __name__ == "__main__":
    asyncio.run(_main())
