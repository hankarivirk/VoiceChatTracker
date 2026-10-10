"""Voice chat event listener and dispatcher."""
import asyncio
from typing import Dict, Any, Optional
from .sessions import session_manager
from ..database.groups import get_group_settings, is_group_logging_enabled
from ..utils.formatting import format_duration, format_session_summary, person_link
from ..utils.logging import logger
from ..utils.telegram_compat import html_parse_mode
from ..config import VC_EVENT_NOTIFICATIONS, VC_EVENT_MESSAGE_TTL_SECONDS


class VoiceChatMonitor:
    def __init__(self, bot_client=None):
        self.bot = bot_client
        self._deletion_tasks = set()

    async def _send_ephemeral_event(self, group_id: int, user_id: int, first_name: str, action: str,
                                    username: str = "", seconds: int | None = None):
        """Post a one-line join/leave notice in the source group."""
        if not VC_EVENT_NOTIFICATIONS or self.bot is None:
            return
        who = person_link(user_id, first_name, username)
        if action == "join":
            text = f"🟢 <b>NEW VC JOIN</b>\n{who}\nUser ID: <code>{int(user_id)}</code>\nAction: Joined"
        else:
            spent = f" · {format_duration(seconds)}" if seconds else ""
            text = f"🔴 <b>VC LEFT</b>\n{who}\nUser ID: <code>{int(user_id)}</code>\nAction: Left{spent}"
        try:
            message = await self.bot.send_message(group_id, text, parse_mode=html_parse_mode(), disable_web_page_preview=True)
        except Exception as exc:
            logger.debug("Could not send temporary VC %s notice in %s: %s", action, group_id, exc)
            return

        cfg = await get_group_settings(group_id)
        ttl = max(0, min(60, int(cfg.get("event_message_ttl_seconds", VC_EVENT_MESSAGE_TTL_SECONDS) or 0)))

        async def delete_later():
            try:
                await asyncio.sleep(ttl)
                await message.delete()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Could not delete temporary VC notice in %s: %s", group_id, exc)

        # A zero TTL means keep the event in chat history. Useful for auditability and
        # avoids a notification disappearing before admins can read it.
        if ttl > 0:
            task = asyncio.create_task(delete_later(), name=f"vc-{action}-notice-{group_id}-{user_id}")
            self._deletion_tasks.add(task)
            task.add_done_callback(self._deletion_tasks.discard)

    async def notify_transition(self, group_id: int, user_id: int, first_name: str, action: str):
        """Send a transition notice without modifying session attendance state.

        Used when a safe authoritative roster reconciliation recovers transitions
        missed by the raw update stream. Per-group notification toggles remain honored.
        """
        if action not in ("join", "leave"):
            return
        cfg = await get_group_settings(group_id)
        if not cfg.get("logging_enabled", True) or user_id in cfg.get("blacklisted_users", []):
            return
        if not cfg.get("notify_on_join" if action == "join" else "notify_on_leave", True):
            return
        await self._send_ephemeral_event(group_id, user_id, first_name, action)

    async def handle_call_start(self, group_id: int):
        """Voice chat began in group."""
        if not await is_group_logging_enabled(group_id):
            return
        await session_manager.get_or_create(group_id)
        logger.info(f"Group call started in {group_id}")

    async def handle_call_end(self, group_id: int):
        """Voice chat terminated in group."""
        summary = await session_manager.end_session(group_id)
        if summary and self.bot:
            cfg = await get_group_settings(group_id)
            if cfg.get("notify_on_end", True):
                text = format_session_summary(summary)
                target = group_id
                try:
                    await self.bot.send_message(target, text, parse_mode=html_parse_mode())
                except Exception as e:
                    logger.warning(f"Failed to send session summary to {target}: {e}")

    async def handle_participant_update(
        self,
        group_id: int,
        user_id: int,
        action: str,  # "join" or "leave"
        first_name: str = "",
        username: str = "",
    ):
        """Handle individual user VC actions."""
        tracking = await is_group_logging_enabled(group_id)
        # A leave is always processed: if tracking is switched off mid-call, people who
        # already joined must still be closed out, or their time would be inflated later.
        if not tracking and action != "leave":
            return

        cfg = await get_group_settings(group_id)
        from ..database.mongo import get_db
        blocked = await (await get_db())["blocked_users"].find_one({"user_id": int(user_id)})
        if blocked and action == "join":
            await session_manager.on_user_join(group_id, user_id, first_name, username)
            session_manager.suppress_user_for_active_sessions(user_id)
            return
        if user_id in cfg.get("blacklisted_users", []) and action != "leave":
            return

        if action == "join":
            joined_now = await session_manager.on_user_join(group_id, user_id, first_name, username)
            if joined_now and cfg.get("notify_on_join", True):
                await self._send_ephemeral_event(group_id, user_id, first_name, "join", username)
        elif action == "leave":
            active = session_manager.sessions.get(group_id)
            person = active.participants.get(user_id) if active else None
            # Read the name and time before the participant is removed from the roster.
            name = (person.first_name if person else "") or first_name
            handle = (person.username if person else "") or username
            seconds = person.duration() if person else None
            left_now = await session_manager.on_user_leave(group_id, user_id)
            if left_now and tracking and cfg.get("notify_on_leave", True):
                await self._send_ephemeral_event(group_id, user_id, name, "leave", handle, seconds)
        # Mic mute/unmute events are intentionally ignored; only VC presence counts.


vc_monitor = VoiceChatMonitor()
