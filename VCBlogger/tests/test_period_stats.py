import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import asyncio
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from VCBlogger.database.mongo import InMemoryDatabase
from VCBlogger.stats import period_stats


class TestPeriodStats(unittest.TestCase):
    def test_occupied_duration_uses_union_of_participant_presence(self):
        row = {"duration": 18000, "participants": [
            {"user_id": 1, "segments": [{"joined_at": 100, "left_at": 200}]},
            {"user_id": 2, "segments": [{"joined_at": 150, "left_at": 250}]},
        ]}
        self.assertEqual(period_stats._occupied_duration(row), 150)

    def test_period_aliases(self):
        self.assertEqual(period_stats.normalize_period("today"), "today")
        self.assertEqual(period_stats.normalize_period("this-week"), "weekly")
        self.assertEqual(period_stats.normalize_period("monthly"), "")   # monthly view was removed
        self.assertEqual(period_stats.normalize_period("lifetime"), "all")
        self.assertEqual(period_stats.normalize_period("nonsense"), "")

    def test_calendar_boundaries_use_configured_timezone(self):
        tz = ZoneInfo("Asia/Kolkata")
        reference = int(datetime(2026, 10, 9, 19, 0, tzinfo=tz).timestamp())
        start, end = period_stats.period_bounds("today", reference)
        self.assertEqual(datetime.fromtimestamp(start, tz).strftime("%Y-%m-%d %H:%M"), "2026-10-09 00:00")
        self.assertEqual(datetime.fromtimestamp(end, tz).strftime("%Y-%m-%d %H:%M"), "2026-10-10 00:00")
        self.assertEqual(period_stats.period_bounds("all", reference), None)

    def test_period_totals_keep_group_scope_separate(self):
        db = InMemoryDatabase("test")
        # Use the current local date so the query and fixture share the same bounds.
        now = int(__import__("time").time())
        start, end = period_stats.period_bounds("today", now)
        db["voice_time_segments"].docs.extend([
            {"_id": "a", "timestamp": start + 60, "group_id": -1001, "user_id": 1,
             "duration_seconds": 120, "first_name": "A"},
            {"_id": "b", "timestamp": start + 120, "group_id": -1002, "user_id": 1,
             "duration_seconds": 300, "first_name": "A"},
            {"_id": "c", "timestamp": end + 1, "group_id": -1001, "user_id": 2,
             "duration_seconds": 999, "first_name": "B"},
        ])
        original = period_stats.get_db
        async def fake_get_db():
            return db
        period_stats.get_db = fake_get_db
        try:
            scoped = asyncio.run(period_stats.get_period_user_totals(-1001, "today"))
            global_rows = asyncio.run(period_stats.get_period_user_totals(None, "today"))
            self.assertEqual([(x["user_id"], x["score"]) for x in scoped], [(1, 120)])
            self.assertEqual([(x["user_id"], x["score"]) for x in global_rows], [(1, 420)])
        finally:
            period_stats.get_db = original


if __name__ == "__main__":
    unittest.main()
