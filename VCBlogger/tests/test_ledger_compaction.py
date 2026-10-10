import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import time
import unittest
from VCBlogger.database.mongo import get_db
from VCBlogger.database.users import add_user_voice_time, get_or_create_user, reconcile_user_aggregate
from VCBlogger.database.maintenance import run_retention_cleanup


class TestLedgerCompaction(unittest.IsolatedAsyncioTestCase):
    async def test_old_ledger_is_preserved_and_lifetime_totals_remain_stable(self):
        db = await get_db()
        uid, group_id = 887002, -100887002
        event_id = "compaction-test-segment-887002"
        await add_user_voice_time(uid, group_id, 123, "Compact Test", "compact_test", event_id=event_id)
        await db["voice_time_segments"].update_one({"_id": event_id}, {"$set": {"timestamp": time.time() - 500 * 86400}})
        await reconcile_user_aggregate(uid)
        await db["maintenance_state"].delete_one({"_id": "voice_ledger_compaction"})

        await run_retention_cleanup()
        self.assertIsNotNone(await db["voice_time_segments"].find_one({"_id": event_id}))
        await reconcile_user_aggregate(uid)
        user = await get_or_create_user(uid)
        self.assertEqual(user["total_duration"], 123)
        self.assertEqual(user["group_durations"][str(group_id)], 123)

        # A repeat cleanup/reconciliation must not add the same compacted batch twice.
        await run_retention_cleanup()
        await reconcile_user_aggregate(uid)
        user = await get_or_create_user(uid)
        self.assertEqual(user["total_duration"], 123)
        self.assertEqual(user["group_durations"][str(group_id)], 123)


if __name__ == "__main__":
    unittest.main()
