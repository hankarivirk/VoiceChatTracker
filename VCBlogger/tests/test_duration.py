import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")
"""Unit tests for duration calculations and formatting."""
import unittest
from VCBlogger.utils.formatting import format_duration


class TestDurationFormatting(unittest.TestCase):
    def test_zero_seconds(self):
        self.assertEqual(format_duration(0), "0s")

    def test_seconds_only(self):
        self.assertEqual(format_duration(45), "45s")

    def test_minutes_and_seconds(self):
        self.assertEqual(format_duration(125), "2m 5s")

    def test_hours_minutes_seconds(self):
        self.assertEqual(format_duration(3665), "1h 1m 5s")

    def test_days_hours_minutes(self):
        self.assertEqual(format_duration(90000), "1d 1h")


if __name__ == "__main__":
    unittest.main()
