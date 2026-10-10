"""Active VC sessions with per-visit accounting and persistent heartbeats."""
import asyncio
import uuid
from typing import Dict, Any, Optional
from ..utils.time_utils import now_ts
from ..database.sessions import create_session, save_session_snapshot, close_session
from ..database.users import add_user_voice_time, mark_user_session
from ..database.groups import get_group_settings
from ..utils.logging import logger


class ActiveParticipant:
    def __init__(self, user_id: int, first_name: str = "", username: str = ""):
        self.user_id = int(user_id)
        self.first_name = first_name or f"User_{user_id}"
        self.username = username or ""
        self.joined_at = now_ts()
        self.last_state_change = self.joined_at
        self.accumulated_seconds = 0
        self.segment_id = uuid.uuid4().hex
        self.record_time = True

    def duration(self) -> int:
        return max(0, int(self.accumulated_seconds + now_ts() - self.last_state_change))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "first_name": self.first_name,
            "username": self.username,
            "joined_at": self.joined_at,
            "duration": self.duration(),
            "segment_id": self.segment_id,
            "record_time": self.record_time,
        }


class ActiveSession:
    def __init__(self, group_id: int, session_id: str):
        self.group_id = int(group_id)
        self.session_id = session_id
        self.started_at = now_ts()
        self.participants: Dict[int, ActiveParticipant] = {}
        # One aggregate per user; a user can leave/rejoin several times during a call.
        self.historical_participants: Dict[int, Dict[str, Any]] = {}
        self.peak_participants = 0
        self.occupied_seconds = 0
        self.occupied_since: Optional[int] = None
        self.suppressed_user_ids: set[int] = set()

    def current_duration(self) -> int:
        extra = max(0, now_ts() - self.occupied_since) if self.occupied_since is not None else 0
        return max(0, int(self.occupied_seconds + extra))

    def add_participant(self, user_id: int, first_name: str = "", username: str = ""):
        user_id = int(user_id)
        if user_id not in self.participants:
            if not self.participants:
                self.occupied_since = now_ts()
            self.participants[user_id] = ActiveParticipant(user_id, first_name, username)
            if user_id in self.suppressed_user_ids:
                self.participants[user_id].record_time = False
            self.peak_participants = max(self.peak_participants, len(self.participants))
        else:
            # Refresh profile data without resetting the participant's join clock.
            p = self.participants[user_id]
            if first_name: p.first_name = first_name
            if username: p.username = username

    def remove_participant(self, user_id: int) -> Optional[int]:
        user_id = int(user_id)
        part = self.participants.pop(user_id, None)
        if part is None:
            return None  # duplicate/out-of-order leave is a no-op
        final_duration = part.duration()
        if not self.participants and self.occupied_since is not None:
            self.occupied_seconds += max(0, now_ts() - self.occupied_since)
            self.occupied_since = None
        if user_id in self.suppressed_user_ids:
            # Keep group occupancy correct, but do not persist this user's current
            # visit after their data was deleted or tracking was disabled.
            return final_duration
        previous = self.historical_participants.get(user_id, {})
        segments = list(previous.get("segments", []))
        segment = {
            "segment_id": part.segment_id,
            "joined_at": part.joined_at,
            "left_at": now_ts(),
            "duration": final_duration,
        }
        segments.append(segment)
        self.historical_participants[user_id] = {
            "user_id": user_id,
            "first_name": part.first_name or previous.get("first_name") or f"User_{user_id}",
            "username": part.username or previous.get("username", ""),
            "joined_at": previous.get("joined_at", part.joined_at),
            "left_at": segment["left_at"],
            "duration": int(previous.get("duration", 0)) + final_duration,
            "visit_count": len(segments),
            "segments": segments,
            "last_segment_id": part.segment_id,
            "record_time": bool(part.record_time),
        }
        return final_duration

    def get_all_roster(self) -> list:
        return [p.to_dict() for p in self.participants.values()]


class SessionManager:
    """In-process session coordinator. Persistent state lives in MongoDB."""
    def __init__(self):
        self.sessions: Dict[int, ActiveSession] = {}
        self._create_locks: Dict[int, asyncio.Lock] = {}

    async def get_or_create(self, group_id: int) -> ActiveSession:
        group_id = int(group_id)
        if group_id in self.sessions:
            return self.sessions[group_id]
        # Telegram handlers run concurrently: a call-start update and the first
        # participant-join can arrive together and would otherwise create two DB sessions.
        lock = self._create_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            if group_id not in self.sessions:
                session_id = await create_session(group_id)
                self.sessions[group_id] = ActiveSession(group_id, session_id)
                logger.info("Started new voice chat session: %s in group %s", session_id, group_id)
            return self.sessions[group_id]

    async def on_user_join(self, group_id: int, user_id: int, first_name: str = "", username: str = ""):
        session = await self.get_or_create(group_id)
        was_present = int(user_id) in session.participants
        session.add_participant(user_id, first_name, username)
        if not was_present:
            await save_session_snapshot(session.session_id, {
                "participants": [p.to_dict() for p in session.participants.values()],
                "peak_participants": session.peak_participants,
                "duration": session.current_duration(),
                "occupied_seconds": session.current_duration(),
                "last_snapshot": now_ts(),
            })
            logger.info("User %s joined VC in %s", user_id, group_id)
            return True
        return False

    async def on_user_leave(self, group_id: int, user_id: int):
        if int(group_id) not in self.sessions:
            return False
        session = self.sessions[int(group_id)]
        dur = session.remove_participant(user_id)
        if dur is None:
            return False
        history = session.historical_participants.get(int(user_id))
        if history is None:
            await save_session_snapshot(session.session_id, {
                "participants": [p.to_dict() for p in session.participants.values()],
                "peak_participants": session.peak_participants,
                "duration": session.current_duration(),
                "occupied_seconds": session.current_duration(),
                "last_snapshot": now_ts(),
                "historical_participants": list(session.historical_participants.values()),
            })
            return True
        segment_id = history["last_segment_id"]
        cfg = await get_group_settings(group_id)
        min_thresh = max(0, int(cfg.get("min_duration", 5)))
        if dur >= min_thresh and history.get("record_time", True):
            history["counted_duration"] = int(history.get("counted_duration", 0)) + dur
            await add_user_voice_time(
                user_id, group_id, dur,
                history.get("first_name", ""), history.get("username", ""),
                event_id=f"{session.session_id}:{user_id}:{segment_id}",
            )
            logger.info("Logged %ss for user %s in group %s", dur, user_id, group_id)
        await save_session_snapshot(session.session_id, {
            "participants": [p.to_dict() for p in session.participants.values()],
            "peak_participants": session.peak_participants,
            "last_snapshot": now_ts(),
            "historical_participants": list(session.historical_participants.values()),
        })
        return True

    def suppress_user_for_active_sessions(self, user_id: int, *, clear_history: bool = False) -> None:
        """Stop personal-time recording for this user without ending group occupancy."""
        uid = int(user_id)
        for session in self.sessions.values():
            session.suppressed_user_ids.add(uid)
            if clear_history:
                session.historical_participants.pop(uid, None)
            elif uid in session.historical_participants:
                session.historical_participants[uid]["record_time"] = False
            participant = session.participants.get(uid)
            if participant is not None:
                participant.record_time = False
                # Start a fresh non-recorded segment so unblocking never credits the
                # interval during which tracking was disabled.
                participant.joined_at = now_ts()
                participant.last_state_change = participant.joined_at
                participant.accumulated_seconds = 0
                participant.segment_id = uuid.uuid4().hex

    def unsuppress_user_for_active_sessions(self, user_id: int) -> None:
        """Resume personal-time recording for a re-enabled user from this moment."""
        uid = int(user_id)
        for session in self.sessions.values():
            session.suppressed_user_ids.discard(uid)
            participant = session.participants.get(uid)
            if participant is not None:
                participant.record_time = True
                participant.joined_at = now_ts()
                participant.last_state_change = participant.joined_at
                participant.accumulated_seconds = 0
                participant.segment_id = uuid.uuid4().hex

    async def reconcile_participants(self, group_id: int, actual_participants: list[Dict[str, Any]]) -> None:
        """Align the in-process roster to an authoritative Telegram snapshot.

        Newly discovered attendees start being timed from reconciliation time; missed
        join/leave timestamps cannot be inferred safely from a current roster snapshot.
        """
        group_id = int(group_id)
        session = self.sessions.get(group_id)
        if session is None and actual_participants:
            session = await self.get_or_create(group_id)
        if session is None:
            return
        actual_by_id = {}
        db = await __import__("VCBlogger.database.mongo", fromlist=["get_db"]).get_db()
        blocked_ids = set()
        async for blocked in db["blocked_users"].find({}):
            try:
                blocked_ids.add(int(blocked.get("user_id", 0) or 0))
            except (TypeError, ValueError):
                pass
        for record in actual_participants:
            try:
                uid = int(record.get("user_id", 0))
                if uid > 0:
                    actual_by_id[uid] = record
            except (TypeError, ValueError):
                continue
        for uid in blocked_ids & set(actual_by_id):
            session.suppressed_user_ids.add(uid)
            if uid in session.participants:
                session.participants[uid].record_time = False
        tracked_ids = set(session.participants)
        actual_ids = set(actual_by_id)
        for uid in sorted(tracked_ids - actual_ids):
            await self.on_user_leave(group_id, uid)
        # Reconciliation can discover hundreds of attendees at startup. Add those
        # participants in memory and persist one heartbeat below, rather than issuing
        # one separate database snapshot write for every person in the roster.
        for uid in sorted(actual_ids - tracked_ids):
            person = actual_by_id[uid]
            session.add_participant(
                uid,
                person.get("first_name", ""),
                person.get("username", ""),
            )
            if uid in blocked_ids:
                session.suppressed_user_ids.add(uid)
                session.participants[uid].record_time = False
        session = self.sessions.get(group_id)
        if session:
            for uid in sorted(actual_ids & set(session.participants)):
                person = actual_by_id[uid]
                participant = session.participants[uid]
                if person.get("first_name"):
                    participant.first_name = str(person["first_name"])
                if person.get("username"):
                    participant.username = str(person["username"])
            await self._save_heartbeat(session)

    async def _save_heartbeat(self, session: ActiveSession) -> None:
        """Persist one session's roster and heartbeat without counting downtime."""
        try:
            await save_session_snapshot(session.session_id, {
                "participants": [p.to_dict() for p in session.participants.values()],
                "peak_participants": session.peak_participants,
                "duration": session.current_duration(),
                "occupied_seconds": session.current_duration(),
                "last_snapshot": now_ts(),
                "historical_participants": list(session.historical_participants.values()),
            })
        except Exception as exc:
            logger.warning("Could not save session heartbeat for %s: %s", session.session_id, exc)

    async def snapshot_all(self) -> None:
        """Persist active rosters/heartbeats; an outage time is never counted as attendance."""
        for session in list(self.sessions.values()):
            await self._save_heartbeat(session)

    async def end_session(self, group_id: int) -> Optional[Dict[str, Any]]:
        group_id = int(group_id)
        session = self.sessions.pop(group_id, None)
        if session is None:
            return None

        # Finish current stints and merge them with earlier visits in the same call.
        cfg = await get_group_settings(group_id)
        min_thresh = max(0, int(cfg.get("min_duration", 5)))
        for uid, participant in list(session.participants.items()):
            duration = session.remove_participant(uid)
            if duration is None:
                continue
            history = session.historical_participants.get(uid)
            if history is None:
                continue
            if duration >= min_thresh and history.get("record_time", True):
                history["counted_duration"] = int(history.get("counted_duration", 0)) + duration
                await add_user_voice_time(
                    uid, group_id, duration, participant.first_name, participant.username,
                    event_id=f"{session.session_id}:{uid}:{participant.segment_id}",
                )

        # Count each person once per call, not once per reconnect/leave segment.
        for uid, record in session.historical_participants.items():
            if int(record.get("counted_duration", 0)) > 0:
                try:
                    await mark_user_session(
                        uid, group_id, session.session_id,
                        record.get("first_name", ""), record.get("username", ""),
                    )
                except Exception as exc:
                    logger.warning("Could not record session attendance for user %s: %s", uid, exc)

        total_dur = session.current_duration()
        all_participants = list(session.historical_participants.values())
        await close_session(session.session_id, total_dur, all_participants, session.peak_participants)
        logger.info("Closed VC session %s (%ss) in group %s", session.session_id, total_dur, group_id)
        return {
            "session_id": session.session_id,
            "group_id": group_id,
            "duration": total_dur,
            "occupied_seconds": total_dur,
            "peak_participants": session.peak_participants,
            "participants": all_participants,
            "start_time": session.started_at,
            "end_time": now_ts(),
        }


session_manager = SessionManager()
