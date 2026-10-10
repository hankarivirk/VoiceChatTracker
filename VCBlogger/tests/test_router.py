import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import sys
import time
import types
import unittest
from unittest.mock import patch

from VCBlogger.bot import router, screens
from VCBlogger.bot.context import AccessDenied, Ctx
from VCBlogger.database.mongo import get_db
from VCBlogger.database.users import add_user_voice_time
from VCBlogger.stats import period_stats
from VCBlogger.vc.sessions import session_manager

GROUP = Ctx(user_id=7001, chat_id=-1007001, is_group=True, first_name="Aman", username="aman")
PRIVATE = Ctx(user_id=7001, chat_id=7001, is_group=False, first_name="Aman", username="aman")
OWNER_DM = Ctx(user_id=7001, chat_id=7001, is_group=False, first_name="Aman", is_owner=True)


class TestParsing(unittest.TestCase):
    def test_old_command_names_still_work(self):
        self.assertEqual(router.parse("/leaderboard").name, "top")
        self.assertEqual(router.parse("/mystats weekly").period, "weekly")
        vctop = router.parse("/vctop today")
        self.assertEqual((vctop.name, vctop.global_scope, vctop.period), ("top", True, "today"))
        self.assertEqual(router.parse("/vcgroupstats").name, "groups")
        self.assertEqual(router.parse("/menu@MyBot").name, "start")

    def test_unknown_commands_are_ignored(self):
        self.assertIsNone(router.parse("/play song"))
        self.assertIsNone(router.parse(""))

    def test_period_scope_and_owner_tokens(self):
        parsed = router.parse("/me weekly global u123")
        self.assertEqual((parsed.name, parsed.period, parsed.global_scope, parsed.owner_id), ("me", "weekly", True, 123))

    def test_bad_period_is_flagged_and_monthly_is_gone(self):
        self.assertTrue(router.parse("/top banana").bad_period)
        self.assertTrue(router.parse("/stats monthly").bad_period)

    def test_start_deeplink_argument_is_harmless(self):
        parsed = router.parse("/start setup")
        self.assertEqual(parsed.name, "start")
        self.assertFalse(parsed.bad_period)

    def test_old_settings_commands_map_to_set(self):
        self.assertEqual(router.parse("/threshold 12").args, ("min", "12"))
        self.assertEqual(router.parse("/vcnotices join_off").args, ("join", "off"))
        self.assertEqual(router.parse("/vcnotices off").args, ("notices", "off"))
        self.assertFalse(router.parse("/settings").mutates)
        self.assertTrue(router.parse("/set join").mutates)

    def test_personal_card_rules(self):
        card = router.parse("/me all u55")
        self.assertTrue(router.is_foreign_card(card, 56))
        self.assertFalse(router.is_foreign_card(card, 55))
        self.assertTrue(router.needs_own_message(router.parse("/me all"), GROUP))
        self.assertFalse(router.needs_own_message(router.parse("/me all u7001"), GROUP))
        self.assertFalse(router.needs_own_message(router.parse("/me all"), PRIVATE))


class TestAccess(unittest.IsolatedAsyncioTestCase):
    async def test_group_only_and_owner_only_commands(self):
        for command in ("/vc", "/history", "/settings"):
            with self.assertRaises(AccessDenied):
                await router.render(command, PRIVATE)
        for command in ("/owner", "/health", "/incidents"):
            with self.assertRaises(AccessDenied):
                await router.render(command, GROUP)

    async def test_private_chat_stats_are_always_global(self):
        screen = await router.render("/stats", PRIVATE)
        self.assertIn("All groups", screen.text)


class TestScreens(unittest.IsolatedAsyncioTestCase):
    async def test_every_public_screen_renders_with_buttons(self):
        for command, ctx in [("/start", GROUP), ("/start", PRIVATE), ("/help", GROUP), ("/help", PRIVATE),
                             ("/vc", GROUP), ("/history", GROUP), ("/stats today", GROUP),
                             ("/top weekly", GROUP), ("/me", GROUP), ("/groups", PRIVATE),
                             ("/settings", GROUP), ("/top", PRIVATE), ("/me", PRIVATE)]:
            screen = await router.render(command, ctx)
            self.assertTrue(screen.text, command)
            self.assertIsNotNone(screen.markup, command)
            self.assertLess(len(screen.text), 4096)

    async def test_bad_period_shows_usage(self):
        screen = await router.render("/top banana", GROUP)
        self.assertIn("weekly", screen.text)

    async def test_owner_screens(self):
        fake = types.ModuleType("VCBlogger.vc.telegram_monitor")
        fake.get_monitor_status = lambda: {"connected_accounts": 2, "last_error": None}
        with patch.dict(sys.modules, {"VCBlogger.vc.telegram_monitor": fake}):
            panel = await router.render("/owner", OWNER_DM)
            health = await router.render("/health", OWNER_DM)
            issues = await router.render("/incidents", OWNER_DM)
        self.assertIn("Owner panel", panel.text)
        self.assertIn("2 of", panel.text)
        self.assertIn("Health", health.text)
        self.assertIn("Recent issues", issues.text)

    async def test_live_roster_is_capped_and_sorted(self):
        group_id = -1007002
        session = await session_manager.get_or_create(group_id)
        for uid in range(1, 41):
            session.add_participant(uid, f"P{uid}", "")
            session.participants[uid].last_state_change -= uid
        text = await screens.live(group_id)
        self.assertIn("40 in call", text)
        self.assertIn("…and 30 more", text)
        self.assertLess(text.index("P40"), text.index("P39"))     # longest time first
        await session_manager.end_session(group_id)

    async def test_viewing_stats_never_creates_users(self):
        db = await get_db()
        before = len(db["users"].docs)
        await router.render("/me all", Ctx(user_id=991122, chat_id=-1007003, is_group=True, first_name="Ghost"))
        await router.render("/me today", Ctx(user_id=991122, chat_id=-1007003, is_group=True, first_name="Ghost"))
        self.assertEqual(len(db["users"].docs), before)
        self.assertIsNone(await db["users"].find_one({"user_id": 991122}))


class TestRecordsStayConsistent(unittest.IsolatedAsyncioTestCase):
    async def test_me_and_top_agree_and_rank_is_shown(self):
        group_id = -1007004
        await add_user_voice_time(7101, group_id, 600, "Top Dog", "top", event_id="r-1")
        await add_user_voice_time(7102, group_id, 120, "Runner", "run", event_id="r-2")
        ctx = Ctx(user_id=7102, chat_id=group_id, is_group=True, first_name="Runner", username="run")
        mine = (await router.render("/me all", ctx)).text
        board = (await router.render("/top all", ctx)).text
        self.assertIn("2m", mine)
        self.assertIn("#2 of 2", mine)
        self.assertLess(board.index("Top Dog"), board.index("Runner"))
        today = (await router.render("/me today", ctx)).text
        self.assertIn("Voice time", today)

    async def test_calls_are_counted_in_the_period_they_ended(self):
        db = await get_db()
        group_id = -1007005
        now = int(time.time())
        start, end = period_stats.period_bounds("today", now)
        db["sessions"].docs.extend([
            # started yesterday, ended today: belongs to today (its voice time is stamped today too)
            {"session_id": "c1", "group_id": group_id, "is_active": False, "start_time": start - 600,
             "end_time": start + 600, "duration": 1200, "peak_participants": 4},
            # ended yesterday: not today
            {"session_id": "c2", "group_id": group_id, "is_active": False, "start_time": start - 7200,
             "end_time": start - 3600, "duration": 3600, "peak_participants": 9},
        ])
        today = await period_stats.get_period_group_summary(group_id, "today")
        self.assertEqual(today["total_sessions"], 1)
        self.assertEqual(today["peak_participants"], 4)
        everything = await period_stats.get_period_group_summary(group_id, "all")
        self.assertEqual(everything["total_sessions"], 2)


if __name__ == "__main__":
    unittest.main()
