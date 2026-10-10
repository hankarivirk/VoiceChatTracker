"""Compressed local backups of primary VCBlogger collections."""
from __future__ import annotations
import asyncio
import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..config import BACKUP_DIR, BACKUP_RETENTION_COUNT, BACKUP_INTERVAL_HOURS, BACKUP_INITIAL_DELAY_SECONDS
from ..utils.logging import logger
from .mongo import get_db

_COLLECTIONS = ("groups", "users", "sessions", "incidents", "attendance",
                "voice_time_segments", "maintenance_state", "blocked_users")
_backup_lock = asyncio.Lock()


def _json_default(value):
    try:
        return str(value)
    except Exception:
        return repr(value)


async def create_backup() -> str:
    """Write a gzip JSON snapshot using streaming output and atomic rename."""
    async with _backup_lock:
        db = await get_db()
        folder = Path(BACKUP_DIR).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = folder / f"vcblogger-backup-{stamp}.json.gz"
        temp = folder / f".{target.name}.tmp"
        try:
            with gzip.open(temp, "wt", encoding="utf-8") as stream:
                stream.write('{"format":1,"created_at":')
                stream.write(json.dumps(stamp))
                stream.write(',"collections":{')
                for index, name in enumerate(_COLLECTIONS):
                    if index:
                        stream.write(',')
                    stream.write(json.dumps(name) + ':[')
                    first = True
                    async for doc in db[name].find({}):
                        if not first:
                            stream.write(',')
                        stream.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=_json_default))
                        first = False
                    stream.write(']')
                stream.write('}}')
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            os.replace(temp, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        backups = sorted(folder.glob("vcblogger-backup-*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[BACKUP_RETENTION_COUNT:]:
            try:
                old.unlink()
            except OSError as exc:
                logger.warning("Could not remove old backup %s: %s", old, exc)
        stat = target.stat()
        try:
            await db["maintenance_state"].update_one({"_id": "backup_status"}, {"$set": {
                "last_backup_at": datetime.now(timezone.utc).timestamp(),
                "last_backup_bytes": stat.st_size, "last_backup_name": target.name,
                "last_backup_success": True,
            }}, upsert=True)
        except Exception as exc:
            logger.warning("Backup file was created but backup status could not be persisted: %s", exc)
        logger.info("Created compressed VCBlogger backup %s (%s collections)", target, len(_COLLECTIONS))
        return str(target)


async def backup_loop(bot=None) -> None:
    """Run an initial backup after startup settles, then daily."""
    from ..config import BACKUP_ENABLED, SUDO_USERS
    if not BACKUP_ENABLED:
        logger.info("Local backups are disabled by BACKUP_ENABLED=false.")
        return
    while True:
        try:
            await asyncio.sleep(BACKUP_INITIAL_DELAY_SECONDS)
            path = await create_backup()
            logger.info("Backup completed: %s", path)
            await asyncio.sleep(max(1, BACKUP_INTERVAL_HOURS * 3600 - BACKUP_INITIAL_DELAY_SECONDS))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Local backup failed: %s", exc)
            try:
                db = await get_db()
                await db["maintenance_state"].update_one({"_id": "backup_status"}, {"$set": {
                    "last_backup_failure_at": datetime.now(timezone.utc).timestamp(),
                    "last_backup_error": str(exc)[:500], "last_backup_success": False,
                }}, upsert=True)
            except Exception:
                pass
            if bot is not None:
                for user_id in SUDO_USERS:
                    try:
                        await bot.send_message(user_id, f"Backup failed: {str(exc)[:200]}\nCheck the logs and the BACKUP_DIR disk.")
                    except Exception:
                        pass
            await asyncio.sleep(3600)
