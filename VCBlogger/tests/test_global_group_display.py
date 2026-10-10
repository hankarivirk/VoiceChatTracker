import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import unittest
from unittest.mock import AsyncMock, patch

from VCBlogger.bot import screens


class TestGlobalGroupDisplay(unittest.IsolatedAsyncioTestCase):
    async def test_group_ranking_never_shows_numeric_id_fallback(self):
        rows = [{"group_id": -1001234567890, "title": "Group_-1001234567890",
                 "formatted_vc_time": "12h", "sessions": 12, "peak": 8}]
        with patch("VCBlogger.bot.screens.get_global_group_breakdown", new=AsyncMock(return_value=rows)):
            text = await screens.groups("all")
        self.assertIn("Unnamed group", text)
        self.assertNotIn("1001234567890", text)

    async def test_group_title_is_escaped_and_ranked(self):
        rows = [{"group_id": -1, "title": "A <b>Club</b>", "formatted_vc_time": "3h", "sessions": 2, "peak": 5}]
        with patch("VCBlogger.bot.screens.get_global_group_breakdown", new=AsyncMock(return_value=rows)):
            text = await screens.groups("weekly")
        self.assertIn("A &lt;b&gt;Club&lt;/b&gt;", text)
        self.assertIn("This week", text)
        self.assertIn("🥇", text)


if __name__ == "__main__":
    unittest.main()
