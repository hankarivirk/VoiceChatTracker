import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")
"""Unit tests for active session management."""
import unittest
import asyncio
from VCBlogger.vc.sessions import SessionManager, ActiveSession
from unittest.mock import patch


class TestSessions(unittest.IsolatedAsyncioTestCase):
    async def test_session_lifecycle(self):
        mgr = SessionManager()
        group_id = -100999
        sess = await mgr.get_or_create(group_id)
        self.assertIsNotNone(sess.session_id)

        # Add participant
        await mgr.on_user_join(group_id, 12345, "Alice", "alice_tg")
        self.assertIn(12345, sess.participants)
        self.assertEqual(sess.peak_participants, 1)

        # Add second participant
        await mgr.on_user_join(group_id, 67890, "Bob", "bob_tg")
        self.assertEqual(sess.peak_participants, 2)

        # Remove participant
        await mgr.on_user_leave(group_id, 12345)
        self.assertNotIn(12345, sess.participants)
        self.assertIn(12345, sess.historical_participants)

        # End session
        res = await mgr.end_session(group_id)
        self.assertIsNotNone(res)
        self.assertEqual(res["group_id"], group_id)
        self.assertEqual(res["peak_participants"], 2)

    def test_group_time_counts_only_while_someone_is_present(self):
        clock = {"now": 100}
        with patch("VCBlogger.vc.sessions.now_ts", side_effect=lambda: clock["now"]):
            session = ActiveSession(-100998, "occupied-test")
            clock["now"] = 200
            self.assertEqual(session.current_duration(), 0)  # VC is empty
            session.add_participant(1, "A")
            clock["now"] = 230
            self.assertEqual(session.current_duration(), 30)
            session.remove_participant(1)
            clock["now"] = 500
            self.assertEqual(session.current_duration(), 30)  # empty again
            session.add_participant(2, "B")
            clock["now"] = 510
            self.assertEqual(session.current_duration(), 40)


if __name__ == "__main__":
    unittest.main()
