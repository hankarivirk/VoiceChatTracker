"""Tests for group-specific attendance counters and multi-visit accounting."""
import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import unittest
from VCBlogger.vc.sessions import ActiveSession
from VCBlogger.database.users import mark_user_session, count_user_sessions, add_user_voice_time, get_top_users
from VCBlogger.stats.user_stats import get_user_stats


class TestMultiVisitAccounting(unittest.TestCase):
    def test_rejoin_segments_accumulate_for_one_user(self):
        session = ActiveSession(-100771, "session-test-multivisit")
        session.add_participant(701, "Test User", "testuser")
        session.participants[701].last_state_change -= 6
        first_duration = session.remove_participant(701)
        self.assertGreaterEqual(first_duration, 6)

        session.add_participant(701, "Test User", "testuser")
        session.participants[701].last_state_change -= 8
        second_duration = session.remove_participant(701)
        self.assertGreaterEqual(second_duration, 8)

        history = session.historical_participants[701]
        self.assertEqual(history["visit_count"], 2)
        self.assertGreaterEqual(history["duration"], first_duration + second_duration)
        self.assertEqual(len(history["segments"]), 2)


class TestAttendanceLedger(unittest.IsolatedAsyncioTestCase):
    async def test_session_count_is_idempotent_and_group_scoped(self):
        user_id = 987654321
        group_a = -100781
        group_b = -100782
        session_a = "unit-test-session-a"
        self.assertTrue(await mark_user_session(user_id, group_a, session_a, "A", "a_user"))
        self.assertFalse(await mark_user_session(user_id, group_a, session_a, "A", "a_user"))
        self.assertEqual(await count_user_sessions(user_id, group_a), 1)
        self.assertEqual(await count_user_sessions(user_id, group_b), 0)

    async def test_duplicate_duration_event_is_not_double_counted(self):
        user_id = 987654323
        group_id = -100785
        await add_user_voice_time(user_id, group_id, 30, "Repeat User", "repeat_user", event_id="same-segment-event")
        await add_user_voice_time(user_id, group_id, 30, "Repeat User", "repeat_user", event_id="same-segment-event")
        stats = await get_user_stats(user_id, group_id)
        self.assertEqual(stats["total_duration"], 30)

    async def test_voice_time_and_group_leaderboards_are_isolated(self):
        user_id = 987654322
        group_a = -100783
        group_b = -100784
        await add_user_voice_time(user_id, group_a, 125, "B User", "b_user", event_id="unit-test-a-1")
        await add_user_voice_time(user_id, group_b, 40, "B User", "b_user", event_id="unit-test-b-1")
        stats_a = await get_user_stats(user_id, group_a)
        stats_b = await get_user_stats(user_id, group_b)
        self.assertEqual(stats_a["total_duration"], 125)
        self.assertEqual(stats_b["total_duration"], 40)
        top_a = await get_top_users(group_a, limit=10)
        top_b = await get_top_users(group_b, limit=10)
        self.assertEqual(top_a[0]["score"], 125)
        self.assertEqual(top_b[0]["score"], 40)


if __name__ == "__main__":
    unittest.main()
