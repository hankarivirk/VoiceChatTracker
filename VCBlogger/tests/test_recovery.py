import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")
"""Unit tests for recovery engine."""
import unittest
from VCBlogger.vc.recovery import RecoveryEngine
from VCBlogger.database.sessions import create_session


class TestRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_flow(self):
        engine = RecoveryEngine()
        # Create a mock dangling session
        sess_id = await create_session(-100888)
        self.assertIsNotNone(sess_id)

        recovered = await engine.recover_dangling_sessions()
        self.assertGreaterEqual(recovered, 1)


if __name__ == "__main__":
    unittest.main()
