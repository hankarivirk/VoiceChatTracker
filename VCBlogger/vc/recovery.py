"""Recover interrupted sessions without crediting downtime as attendance."""
from ..database.sessions import get_active_sessions, close_session
from ..database.users import add_user_voice_time, mark_user_session
from ..database.groups import get_group_settings
from ..database.incidents import log_incident
from ..utils.time_utils import now_ts
from ..utils.logging import logger


class RecoveryEngine:
    async def recover_dangling_sessions(self) -> int:
        active = await get_active_sessions()
        recovered_count = 0
        now = now_ts()

        for sess in active:
            session_id = sess.get("session_id")
            group_id = sess.get("group_id")
            start_time = sess.get("start_time", now)
            last_snapshot = sess.get("last_snapshot", start_time)
            saved_dur = sess.get("occupied_seconds", sess.get("duration"))
            if saved_dur is not None:
                recorded_duration = max(0, int(saved_dur))
            else:
                recorded_duration = max(0, min(now, int(last_snapshot)) - int(start_time))
            active_participants = sess.get("participants", []) or []
            historical = sess.get("historical_participants", []) or []
            by_user = {int(p.get("user_id", 0)): dict(p) for p in historical if p.get("user_id")}

            # The last saved participant objects already contain duration as of their
            # heartbeat. Persist those saved durations before closing the interrupted call.
            settings = await get_group_settings(int(group_id))
            min_duration = max(0, int(settings.get("min_duration", 5)))
            for participant in active_participants:
                try:
                    uid = int(participant.get("user_id", 0))
                    if uid <= 0:
                        continue
                    duration = max(0, int(participant.get("duration", 0)))
                    joined_at = participant.get("joined_at", last_snapshot)
                    segment_id = participant.get("segment_id") or f"recovery:{session_id}:{uid}:{joined_at}"
                    counted_duration = duration if duration >= min_duration else 0
                    if counted_duration > 0:
                        await add_user_voice_time(
                            uid, int(group_id), counted_duration,
                            participant.get("first_name", ""), participant.get("username", ""),
                            event_id=f"{session_id}:{uid}:{segment_id}",
                        )
                    old = by_user.get(uid, {})
                    existing_segments = list(old.get("segments", []))
                    # The last saved heartbeat is a recovered, completed attendance segment.
                    existing_segments.append({
                        "segment_id": segment_id,
                        "joined_at": joined_at,
                        "left_at": last_snapshot,
                        "duration": duration,
                        "recovered": True,
                    })
                    by_user[uid] = {
                        **old,
                        "user_id": uid,
                        "first_name": participant.get("first_name") or old.get("first_name") or f"User_{uid}",
                        "username": participant.get("username") or old.get("username", ""),
                        "joined_at": old.get("joined_at", joined_at),
                        "left_at": last_snapshot,
                        "duration": int(old.get("duration", 0)) + duration,
                        "counted_duration": int(old.get("counted_duration", 0)) + counted_duration,
                        "visit_count": len(existing_segments),
                        "segments": existing_segments,
                    }
                except Exception as exc:
                    logger.warning("Could not reconcile participant during recovery for %s: %s", session_id, exc)

            participants = list(by_user.values())
            for record in participants:
                if int(record.get("counted_duration", 0)) > 0:
                    try:
                        await mark_user_session(
                            int(record["user_id"]), int(group_id), str(session_id),
                            record.get("first_name", ""), record.get("username", ""),
                        )
                    except Exception as exc:
                        logger.warning("Could not recover attendance count for session %s: %s", session_id, exc)

            peak = sess.get("peak_participants", len(active_participants))
            await close_session(session_id, recorded_duration, participants, peak)
            await log_incident(
                group_id=group_id,
                incident_type="UNSCHEDULED_RESTART",
                details=(f"Recovered interrupted session {session_id}. Recorded session duration through the "
                         f"last saved heartbeat ({recorded_duration}s); time after that heartbeat was excluded."),
                severity="info",
            )
            logger.info("Recovered dangling session %s for group %s", session_id, group_id)
            recovered_count += 1

        return recovered_count


recovery_engine = RecoveryEngine()
