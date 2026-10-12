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


if __name__ == "__main__":
    unittest.main()
