import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")
"""Unit tests for user tiers and stats calculations."""
import unittest
from VCBlogger.stats.user_stats import compute_user_tier


class TestStatsTiers(unittest.TestCase):
    def test_bronze_tier(self):
        self.assertEqual(compute_user_tier(100), "🥉 Bronze")

    def test_silver_tier(self):
        self.assertEqual(compute_user_tier(5 * 3600), "🥈 Silver")

    def test_gold_tier(self):
        self.assertEqual(compute_user_tier(25 * 3600), "🥇 Gold")

    def test_diamond_tier(self):
        self.assertEqual(compute_user_tier(55 * 3600), "💎 Diamond")

    def test_legend_tier(self):
        self.assertEqual(compute_user_tier(120 * 3600), "👑 Legend")


if __name__ == "__main__":
    unittest.main()
