import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import unittest
from VCBlogger.database.mongo import get_db
from VCBlogger.database.maintenance import run_retention_cleanup

class TestStorageRetention(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_keeps_active_sessions_and_group_isolation(self):
        db = await get_db()
        now = __import__("time").time()
        # Unique test IDs avoid depending on global test order.
        await db["sessions"].insert_one({
            "session_id": "retention-active-a", "group_id": -99101,
            "start_time": now - 400 * 86400, "end_time": None, "is_active": True,
        })
        await db["sessions"].insert_one({
            "session_id": "retention-old-a", "group_id": -99101,
            "start_time": now - 400 * 86400, "end_time": now - 399 * 86400, "is_active": False,
        })
        await db["sessions"].insert_one({
            "session_id": "retention-old-b", "group_id": -99102,
            "start_time": now - 400 * 86400, "end_time": now - 399 * 86400, "is_active": False,
        })
        await run_retention_cleanup()
        self.assertIsNotNone(await db["sessions"].find_one({"session_id": "retention-active-a"}))
        self.assertIsNotNone(await db["sessions"].find_one({"session_id": "retention-old-a"}))
        self.assertIsNotNone(await db["sessions"].find_one({"session_id": "retention-old-b"}))

    async def test_current_month_history_is_not_deleted(self):
        from VCBlogger.database.maintenance import current_month_start_ts
        db = await get_db()
        month_start = current_month_start_ts()
        await db["sessions"].insert_one({
            "session_id": "retention-current-month", "group_id": -99103,
            "start_time": month_start + 100, "end_time": month_start + 200,
            "is_active": False,
        })
        await db["incidents"].insert_one({
            "incident_id": "retention-current-incident", "group_id": -99103,
            "timestamp": month_start + 100, "type": "test",
        })
        await run_retention_cleanup()
        self.assertIsNotNone(await db["sessions"].find_one({"session_id": "retention-current-month"}))
        self.assertIsNotNone(await db["incidents"].find_one({"incident_id": "retention-current-incident"}))


    async def test_old_attendance_details_are_preserved_for_all_time_history(self):
        from VCBlogger.database.users import mark_user_session, count_user_sessions
        db = await get_db()
        now = __import__("time").time()
        user_id = 99202
        group_id = -99202
        completed_session = "retention-attendance-completed"
        active_session = "retention-attendance-active"
        await mark_user_session(user_id, group_id, completed_session, "Retention User", "retention_user")
        await db["attendance"].update_one(
            {"_id": f"{completed_session}:{user_id}"},
            {"$set": {"recorded_at": now - 300 * 86400}},
        )
        await db["sessions"].insert_one({
            "session_id": active_session, "group_id": group_id,
            "start_time": now - 400 * 86400, "end_time": None, "is_active": True,
        })
        await db["attendance"].insert_one({
            "_id": f"{active_session}:{user_id}", "session_id": active_session,
            "group_id": group_id, "user_id": user_id, "recorded_at": now - 300 * 86400,
        })
        await run_retention_cleanup()
        self.assertIsNotNone(await db["attendance"].find_one({"_id": f"{completed_session}:{user_id}"}))
        self.assertIsNotNone(await db["attendance"].find_one({"_id": f"{active_session}:{user_id}"}))
        self.assertEqual(await count_user_sessions(user_id, group_id), 1)

    async def test_incidents_are_preserved_by_non_destructive_cleanup(self):
        db = await get_db()
        now = __import__("time").time()
        await db["incidents"].insert_one({
            "incident_id": "retention-inc-a", "group_id": -99201,
            "timestamp": now - 400 * 86400, "type": "test",
        })
        await run_retention_cleanup()
        self.assertIsNotNone(await db["incidents"].find_one({"incident_id": "retention-inc-a"}))

if __name__ == "__main__":
    unittest.main()
