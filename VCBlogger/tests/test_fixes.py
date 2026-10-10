import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import asyncio
import enum
import unittest

from VCBlogger.database.document_backends import RedisDatabase, make_doc_id, direct_doc_id
from VCBlogger.database.groups import get_group_settings, update_group_settings
from VCBlogger.utils.telegram_compat import is_group_chat, is_admin_status
from VCBlogger.vc.sessions import SessionManager


class FakeRedis:
    def __init__(self):
        self.h = {}

    async def hset(self, k, f, v): self.h.setdefault(k, {})[f] = v
    async def hget(self, k, f): return self.h.get(k, {}).get(f)
    async def hvals(self, k): return list(self.h.get(k, {}).values())
    async def hdel(self, k, f): self.h.get(k, {}).pop(f, None)


class TestAdapterIdentity(unittest.IsolatedAsyncioTestCase):
    async def test_incidents_in_same_group_do_not_overwrite(self):
        db = RedisDatabase("t", FakeRedis())
        for i in range(3):
            await db["incidents"].insert_one({"incident_id": f"i{i}", "group_id": -100, "type": "X"})
        self.assertEqual(await db["incidents"].count_documents({}), 3)
        self.assertEqual((await db["incidents"].find_one({"incident_id": "i1"}))["incident_id"], "i1")

    def test_keys_are_collection_aware(self):
        self.assertEqual(make_doc_id("incidents", {"incident_id": "a", "group_id": 1}), "incident_id:a")
        self.assertEqual(make_doc_id("attendance", {"_id": "s:1", "session_id": "s"}), "_id:s:1")
        self.assertEqual(direct_doc_id("users", {"user_id": 5}), "user_id:5")
        self.assertIsNone(direct_doc_id("users", {"group_id": 5}))
        self.assertIsNone(direct_doc_id("users", {"user_id": 5, "x": 1}))

    async def test_concurrent_increments_are_not_lost(self):
        db = RedisDatabase("t", FakeRedis())
        users = db["users"]
        await users.update_one({"user_id": 1}, {"$set": {"total": 0}}, upsert=True)
        await asyncio.gather(*[users.update_one({"user_id": 1}, {"$inc": {"total": 1}}) for _ in range(25)])
        self.assertEqual((await users.find_one({"user_id": 1}))["total"], 25)

    async def test_direct_lookup_still_checks_query(self):
        db = RedisDatabase("t", FakeRedis())
        await db["users"].insert_one({"user_id": 9, "name": "x"})
        self.assertIsNone(await db["users"].find_one({"user_id": 10}))


class TestGroupDefaults(unittest.IsolatedAsyncioTestCase):
    async def test_partial_group_doc_gets_defaults(self):
        gid = -100555123
        # Same call the bot makes on the first command in a group (title only).
        await update_group_settings(gid, {"title": "Test Group"})
        cfg = await get_group_settings(gid)
        self.assertTrue(cfg["logging_enabled"])
        self.assertTrue(cfg["notify_on_end"])
        self.assertIn("min_duration", cfg)
        self.assertEqual(cfg["title"], "Test Group")


class TestTelegramCompat(unittest.TestCase):
    def test_enum_and_string_values(self):
        class ChatType(enum.Enum):
            PRIVATE = "private"
            SUPERGROUP = "supergroup"

        class Status(enum.Enum):
            OWNER = "owner"
            MEMBER = "member"

        class Chat:
            def __init__(self, t): self.type = t

        self.assertTrue(is_group_chat(Chat(ChatType.SUPERGROUP)))
        self.assertTrue(is_group_chat(Chat("group")))
        self.assertFalse(is_group_chat(Chat(ChatType.PRIVATE)))
        self.assertTrue(is_admin_status(Status.OWNER))
        self.assertTrue(is_admin_status("administrator"))
        self.assertFalse(is_admin_status(Status.MEMBER))


class TestSessionCreationRace(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_get_or_create_makes_one_session(self):
        mgr = SessionManager()
        sessions = await asyncio.gather(*[mgr.get_or_create(-100777) for _ in range(10)])
        self.assertEqual(len({s.session_id for s in sessions}), 1)




class TestV420Fixes(unittest.IsolatedAsyncioTestCase):
    def test_owner_sub_keyboard_targets_correct_command(self):
        from VCBlogger.bot import keyboards
        health_markup = keyboards.owner_sub("health")
        incidents_markup = keyboards.owner_sub("incidents")
        health_data = [b.callback_data for row in health_markup.inline_keyboard for b in row if b.callback_data]
        incidents_data = [b.callback_data for row in incidents_markup.inline_keyboard for b in row if b.callback_data]
        self.assertIn("nav|/health", health_data)
        self.assertIn("nav|/incidents", incidents_data)

    async def test_backup_and_restore_includes_blocked_users(self):
        import tempfile, gzip, json
        from pathlib import Path
        from unittest.mock import patch
        from VCBlogger.database.mongo import get_db
        from VCBlogger.database.backup import create_backup
        from VCBlogger.database.restore import restore

        db = await get_db()
        await db["blocked_users"].update_one(
            {"user_id": 998877},
            {"$set": {"user_id": 998877, "blocked": True}},
            upsert=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch("VCBlogger.database.backup.BACKUP_DIR", tmp):
                backup_path = Path(await create_backup())
            with gzip.open(backup_path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            self.assertIn("blocked_users", payload["collections"])
            self.assertTrue(any(d.get("user_id") == 998877 for d in payload["collections"]["blocked_users"]))

            await db["blocked_users"].delete_one({"user_id": 998877})
            self.assertIsNone(await db["blocked_users"].find_one({"user_id": 998877}))

            await restore(backup_path)
            restored = await db["blocked_users"].find_one({"user_id": 998877})
            self.assertIsNotNone(restored)
            self.assertTrue(restored.get("blocked"))

    async def test_delete_user_data_cleans_historical_participants(self):
        from VCBlogger.database.mongo import get_db
        from VCBlogger.database.users import delete_user_data
        db = await get_db()
        uid = 554433
        session_id = "test-session-historical-clean"
        await db["sessions"].insert_one({
            "session_id": session_id,
            "group_id": -100999,
            "participants": [{"user_id": uid, "first_name": "Gone"}],
            "historical_participants": [{"user_id": uid, "first_name": "Gone"}],
        })
        await delete_user_data(uid)
        updated = await db["sessions"].find_one({"session_id": session_id})
        self.assertEqual(updated.get("participants"), [])
        self.assertEqual(updated.get("historical_participants"), [])

    async def test_live_call_duration_syncs_between_top_and_me(self):
        from VCBlogger.vc.sessions import session_manager
        from VCBlogger.stats import period_stats
        group_id = -1006611
        user_id = 112233
        session = await session_manager.get_or_create(group_id)
        session.add_participant(user_id, "LiveUser", "liveuser")
        session.participants[user_id].last_state_change -= 180

        top_today = await period_stats.get_period_user_totals(group_id, "today")
        top_all = await period_stats.get_period_user_totals(group_id, "all")
        me_today = await period_stats.get_period_user_stats(user_id, group_id, "today")
        me_all = await period_stats.get_period_user_stats(user_id, group_id, "all")

        self.assertTrue(any(u["user_id"] == user_id and u["score"] >= 180 for u in top_today))
        self.assertTrue(any(u["user_id"] == user_id and u["score"] >= 180 for u in top_all))
        self.assertGreaterEqual(me_today["total_duration"], 180)
        self.assertGreaterEqual(me_all["total_duration"], 180)

        await session_manager.end_session(group_id)

    def test_format_when_handles_strings_and_floats(self):
        import time
        from VCBlogger.utils.formatting import format_when
        now = time.time()
        self.assertNotEqual(format_when(str(now)), "-")
        self.assertNotEqual(format_when(now), "-")
        self.assertEqual(format_when("invalid-timestamp"), "-")
        self.assertEqual(format_when(None), "-")

    async def test_ensure_assistants_when_bot_is_group_creator(self):
        from unittest.mock import AsyncMock, patch
        from VCBlogger.vc.telegram_monitor import ensure_assistants_in_group
        fake_bot = type("FakeBot", (), {})()
        fake_bot.me = type("Me", (), {"id": 123456})()
        member = type("Member", (), {"status": "creator", "privileges": None})()
        fake_bot.get_chat_member = AsyncMock(return_value=member)
        invite_obj = type("Invite", (), {"invite_link": "https://t.me/+joinlink"})()
        fake_bot.create_chat_invite_link = AsyncMock(return_value=invite_obj)
        fake_bot.revoke_chat_invite_link = AsyncMock()

        fake_assistant = type("Assistant", (), {})()
        fake_assistant.me = type("Me", (), {"id": 789012})()
        fake_assistant.get_chat_member = AsyncMock(side_effect=Exception("USER_NOT_PARTICIPANT"))
        fake_assistant.join_chat = AsyncMock()

        with patch("VCBlogger.vc.telegram_monitor._connected_monitor_clients", {789012: fake_assistant}):
            result = await ensure_assistants_in_group(fake_bot, -1001122)
            self.assertTrue(result["ok"])
            self.assertEqual(result["joined"], [789012])


if __name__ == "__main__":
    unittest.main()
