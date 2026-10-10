import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")
"""Unit tests for periodic session snapshots."""
import unittest
from VCBlogger.vc.sessions import ActiveSession
from VCBlogger.utils.time_utils import now_ts


class TestSessionSnapshot(unittest.TestCase):
    def test_participant_duration_accumulation(self):
        sess = ActiveSession(-100777, "test-sess-1")
        sess.add_participant(555, "Charlie", "charlie_tg")
        self.assertIn(555, sess.participants)
        p = sess.participants[555]
        self.assertGreaterEqual(p.duration(), 0)


if __name__ == "__main__":
    unittest.main()
