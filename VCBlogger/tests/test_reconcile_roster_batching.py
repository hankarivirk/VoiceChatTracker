"""Regression test: a startup roster sync should not perform one write per user."""
import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import unittest
from unittest.mock import AsyncMock, patch

from VCBlogger.vc.sessions import SessionManager


class TestReconcileRosterBatching(unittest.IsolatedAsyncioTestCase):
    async def test_reconcile_roster_batches_new_participant_heartbeat(self):
        manager = SessionManager()
        group_id = -100987654321
        roster = [
            {"user_id": 700001 + index, "first_name": f"User {index}", "username": f"user_{index}"}
            for index in range(75)
        ]

        with patch("VCBlogger.vc.sessions.save_session_snapshot", new_callable=AsyncMock) as heartbeat:
            await manager.reconcile_participants(group_id, roster)

        session = manager.sessions[group_id]
        self.assertEqual(len(session.participants), len(roster))
        self.assertEqual(session.peak_participants, len(roster))
        self.assertEqual(heartbeat.await_count, 1)
        self.assertNotIn("is_muted", session.participants[700001].to_dict())
        self.assertNotIn("is_muted", session.participants[700002].to_dict())
        # Leave no active database session behind for unrelated test cases.
        await manager.end_session(group_id)
