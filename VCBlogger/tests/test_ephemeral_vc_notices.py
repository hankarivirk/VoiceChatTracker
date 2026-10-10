import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from VCBlogger.bot.admin import apply_setting
from VCBlogger.database.groups import get_group_settings, update_group_settings
from VCBlogger.vc.monitor import VoiceChatMonitor
from VCBlogger.vc.sessions import session_manager


def _fake_bot(sent):
    bot = type("FakeBot", (), {})()

    async def send_message(chat_id, text, **kwargs):
        message = type("FakeMessage", (), {})()
        message.delete = AsyncMock()
        message.text = text
        sent.append(message)
        return message

    bot.send_message = AsyncMock(side_effect=send_message)
    return bot


class TestVCNotices(unittest.IsolatedAsyncioTestCase):
    async def test_notices_are_short_and_leave_shows_time_spent(self):
        sent = []
        monitor = VoiceChatMonitor(_fake_bot(sent))
        group_id, user_id = -100887001, 887001
        with patch("VCBlogger.vc.monitor.VC_EVENT_MESSAGE_TTL_SECONDS", 0):
            await monitor.handle_participant_update(group_id, user_id, "join", "Join Test", "join_test")
            await monitor.handle_participant_update(group_id, user_id, "join", "Join Test", "join_test")  # duplicate
            session_manager.sessions[group_id].participants[user_id].last_state_change -= 125
            await monitor.handle_participant_update(group_id, user_id, "leave", "Join Test", "join_test")
        self.assertEqual(len(sent), 2)
        self.assertIn("NEW VC JOIN", sent[0].text)
        self.assertIn("User ID", sent[0].text)
        self.assertIn("Action: Joined", sent[0].text)
        self.assertIn("Join Test", sent[0].text)
        self.assertIn("887001</code>", sent[0].text)
        self.assertIn("Action: Left", sent[1].text)
        self.assertIn("2m", sent[1].text)
        await session_manager.end_session(group_id)

    async def test_placeholder_names_are_never_shown(self):
        sent = []
        monitor = VoiceChatMonitor(_fake_bot(sent))
        with patch("VCBlogger.vc.monitor.VC_EVENT_MESSAGE_TTL_SECONDS", 0):
            await monitor.handle_participant_update(-100887002, 887002, "join", "User_887002", "")
        self.assertIn("Unknown user", sent[0].text)
        await session_manager.end_session(-100887002)

    async def test_leave_is_still_recorded_after_tracking_is_switched_off(self):
        group_id, user_id = -100887003, 887003
        monitor = VoiceChatMonitor(_fake_bot([]))
        await monitor.handle_participant_update(group_id, user_id, "join", "Late Leaver")
        await update_group_settings(group_id, {"logging_enabled": False})
        await monitor.handle_participant_update(group_id, user_id, "leave", "Late Leaver")
        self.assertNotIn(user_id, session_manager.sessions[group_id].participants)
        await session_manager.end_session(group_id)


class TestSettingChanges(unittest.IsolatedAsyncioTestCase):
    async def test_toggles_are_group_scoped_and_independent(self):
        group_a, group_b = -100887011, -100887012
        await apply_setting(group_a, ["join"])
        cfg_a, cfg_b = await get_group_settings(group_a), await get_group_settings(group_b)
        self.assertFalse(cfg_a["notify_on_join"])
        self.assertTrue(cfg_a["notify_on_leave"])
        self.assertTrue(cfg_b["notify_on_join"])
        await apply_setting(group_a, ["join"])
        self.assertTrue((await get_group_settings(group_a))["notify_on_join"])

    async def test_minimum_time_is_validated(self):
        group_id = -100887013
        self.assertIn("Minimum time: 10s", await apply_setting(group_id, ["min", "10"]))
        self.assertEqual((await get_group_settings(group_id))["min_duration"], 10)
        self.assertIn("between 0 and", await apply_setting(group_id, ["min", "99999"]))
        self.assertEqual((await get_group_settings(group_id))["min_duration"], 10)

    async def test_reading_settings_does_not_create_a_group_record(self):
        from VCBlogger.database.mongo import get_db
        group_id = -100887014
        await get_group_settings(group_id)
        self.assertIsNone(await (await get_db())["groups"].find_one({"group_id": group_id}))


if __name__ == "__main__":
    unittest.main()
