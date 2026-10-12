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

    def test_legacy_empty_call_uptime_is_never_used_as_occupied_time(self):
        self.assertEqual(period_stats._occupied_duration({"duration": 18 * 3600}), 0)

    def test_all_time_uses_occupied_counter_but_periods_clip_presence_intervals(self):
        row = {"start_time": 100, "end_time": 400, "duration": 300, "occupied_seconds": 180,
               "duration_type": "occupied_presence",
               "participants": [{"user_id": 1, "segments": [{"joined_at": 150, "left_at": 350}]}]}
        self.assertEqual(period_stats._occupied_duration(row), 180)
        self.assertEqual(period_stats._occupied_duration(row, (200, 300)), 100)

    def test_occupied_counter_without_intervals_only_counts_when_session_is_inside_period(self):
        row = {"start_time": 100, "end_time": 400, "occupied_seconds": 180,
               "duration_type": "occupied_presence"}
        self.assertEqual(period_stats._occupied_duration(row, (90, 500)), 180)
        self.assertEqual(period_stats._occupied_duration(row, (200, 500)), 0)

    def test_unmarked_legacy_occupied_counter_is_not_assumed_to_be_presence_time(self):
        self.assertEqual(period_stats._occupied_duration({"duration": 18 * 3600, "occupied_seconds": 18 * 3600}), 0)

    def test_group_ranking_does_not_count_legacy_raw_vc_uptime(self):
        db = InMemoryDatabase("group-ranking-legacy")
        db["sessions"].docs.append({
            "session_id": "legacy-empty-call", "group_id": -100555, "is_active": False,
            "start_time": 1, "end_time": 9999999999, "duration": 18 * 3600,
            "occupied_seconds": 18 * 3600, "participants": [],
        })
        original = period_stats.get_db
        async def fake_get_db():
            return db
        period_stats.get_db = fake_get_db
        try:
            rows = asyncio.run(period_stats.get_global_group_breakdown("all"))
            self.assertEqual(rows, [])
        finally:
            period_stats.get_db = original

    def test_live_group_ranking_uses_presence_and_ignores_empty_calls(self):
        from unittest.mock import patch
        from VCBlogger.vc.sessions import ActiveSession, session_manager
        db = InMemoryDatabase("group-ranking-live")
        db["groups"].docs.append({"group_id": -100556, "title": "Presence Test Group"})
        clock = {"now": 100}
        active = ActiveSession(-100556, "live-presence-test")
        active.started_at = 1
        with patch("VCBlogger.vc.sessions.now_ts", side_effect=lambda: clock["now"]), \
             patch("VCBlogger.stats.period_stats.now_ts", side_effect=lambda: clock["now"]), \
             patch.object(session_manager, "sessions", {-100556: active}):
            original = period_stats.get_db
            async def fake_get_db():
                return db
            period_stats.get_db = fake_get_db
            try:
                empty = asyncio.run(period_stats.get_global_group_breakdown("all"))
                self.assertEqual(empty, [])
                active.add_participant(101, "Alice", "alice")
                clock["now"] = 130
                rows = asyncio.run(period_stats.get_global_group_breakdown("all"))
                self.assertEqual(rows[0]["title"], "Presence Test Group")
                self.assertEqual(rows[0]["vc_seconds"], 30)
                self.assertEqual(rows[0]["sessions"], 1)
            finally:
                period_stats.get_db = original

    def test_all_time_user_ranking_includes_current_presence_before_leave(self):
        from unittest.mock import AsyncMock, patch
        from VCBlogger.vc.sessions import ActiveSession, session_manager
        clock = {"now": 100}
        active = ActiveSession(-100557, "live-user-test")
        with patch("VCBlogger.vc.sessions.now_ts", side_effect=lambda: clock["now"]), \
             patch("VCBlogger.stats.period_stats.get_top_users", new=AsyncMock(return_value=[{
                 "user_id": 202, "first_name": "Alice", "score": 40, "total_duration": 40,
             }])), \
             patch.object(session_manager, "sessions", {-100557: active}):
            active.add_participant(202, "Alice", "alice")
            clock["now"] = 130
            rows = asyncio.run(period_stats.get_period_user_totals(None, "all"))
            self.assertEqual(rows[0]["user_id"], 202)
            self.assertEqual(rows[0]["score"], 70)

    def test_interval_ledger_clips_overnight_visits_and_keeps_legacy_rows(self):
        db = InMemoryDatabase("interval-ledger")
        db["voice_time_segments"].docs.extend([
            {"_id": "cross-before", "group_id": -1001, "user_id": 1, "timestamp": 150,
             "start_time": 50, "end_time": 150, "duration_seconds": 100},
            {"_id": "cross-after", "group_id": -1001, "user_id": 2, "timestamp": 250,
             "start_time": 190, "end_time": 250, "duration_seconds": 60},
            {"_id": "legacy", "group_id": -1001, "user_id": 3, "timestamp": 150,
             "duration_seconds": 20},
            {"_id": "outside", "group_id": -1001, "user_id": 4, "timestamp": 250,
             "duration_seconds": 10},
        ])
        async def run():
            rows = [row async for row in period_stats._period_ledger_rows(db, {"group_id": -1001}, (100, 200))]
            return {row["_id"]: period_stats._ledger_seconds_for_period(row, (100, 200)) for row in rows}
        self.assertEqual(asyncio.run(run()), {"cross-before": 50, "cross-after": 10, "legacy": 20})

    def test_period_aliases(self):
        self.assertEqual(period_stats.normalize_period("today"), "today")
        self.assertEqual(period_stats.normalize_period("this-week"), "weekly")
        self.assertEqual(period_stats.normalize_period("monthly"), "monthly")
        self.assertEqual(period_stats.normalize_period("month"), "monthly")
        self.assertEqual(period_stats.normalize_period("this-month"), "monthly")
        self.assertEqual(period_stats.normalize_period("lifetime"), "all")
        self.assertEqual(period_stats.normalize_period("nonsense"), "")

    def test_calendar_boundaries_use_configured_timezone(self):
        tz = ZoneInfo("Asia/Kolkata")
        reference = int(datetime(2026, 10, 9, 19, 0, tzinfo=tz).timestamp())
        start, end = period_stats.period_bounds("today", reference)
        self.assertEqual(datetime.fromtimestamp(start, tz).strftime("%Y-%m-%d %H:%M"), "2026-10-09 00:00")
        self.assertEqual(datetime.fromtimestamp(end, tz).strftime("%Y-%m-%d %H:%M"), "2026-10-10 00:00")
        self.assertEqual(period_stats.period_bounds("all", reference), None)
        month_start, month_end = period_stats.period_bounds("monthly", reference)
        self.assertEqual(datetime.fromtimestamp(month_start, tz).strftime("%Y-%m-%d %H:%M"), "2026-10-01 00:00")
        self.assertEqual(datetime.fromtimestamp(month_end, tz).strftime("%Y-%m-%d %H:%M"), "2026-11-01 00:00")

    def test_month_bounds_roll_over_year_correctly_and_accept_epoch_zero(self):
        tz = ZoneInfo("Asia/Kolkata")
        december = int(datetime(2026, 12, 31, 23, 59, tzinfo=tz).timestamp())
        start, end = period_stats.period_bounds("monthly", december)
        self.assertEqual(datetime.fromtimestamp(start, tz).strftime("%Y-%m-%d %H:%M"), "2026-12-01 00:00")
        self.assertEqual(datetime.fromtimestamp(end, tz).strftime("%Y-%m-%d %H:%M"), "2027-01-01 00:00")
        # An explicit timestamp of zero is a valid reference, not an instruction to use now.
        start, end = period_stats.period_bounds("today", 0)
        self.assertEqual(end - start, 86400)
        self.assertLess(start, 86400)

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
