import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import unittest

from VCBlogger.bot import keyboards


def _data(markup):
    return [b.callback_data.removeprefix("nav|") for row in markup.inline_keyboard for b in row if b.callback_data]


def _labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


class TestKeyboards(unittest.TestCase):
    def test_group_home_has_the_six_main_actions(self):
        data = _data(keyboards.home(is_group=True))
        self.assertEqual(data, ["/vc", "/stats", "/top", "/groups", "/me", "/help"])

    def test_private_home_is_global_only_and_owner_panel_is_owner_only(self):
        plain = _data(keyboards.home(is_group=False))
        self.assertIn("/top", plain)
        self.assertIn("/groups", plain)
        self.assertNotIn("/vc", plain)
        self.assertNotIn("/owner", plain)
        self.assertIn("/owner", _data(keyboards.home(is_group=False, is_owner=True)))

    def test_period_tabs_mark_the_current_period_and_never_offer_monthly(self):
        markup = keyboards.top("weekly", is_group=True, glob=False)
        self.assertIn("• Week", _labels(markup))
        data = _data(markup)
        for period in ("today", "weekly", "all"):
            self.assertIn(f"/top {period}", data)
        self.assertFalse(any("month" in item for item in data))

    def test_scope_button_switches_between_group_and_all_groups(self):
        self.assertIn("/stats all global", _data(keyboards.stats("all", True, False)))
        self.assertIn("/stats all", _data(keyboards.stats("all", True, True)))

    def test_personal_card_buttons_are_locked_to_the_owner(self):
        for item in _data(keyboards.me("all", True, False, owner=42)):
            self.assertTrue(item.endswith(" u42"), item)

    def test_settings_buttons_show_state_and_current_minimum(self):
        cfg = {"logging_enabled": True, "notify_on_join": False, "notify_on_leave": True,
               "notify_on_end": True, "min_duration": 10}
        markup = keyboards.settings(cfg)
        labels = _labels(markup)
        self.assertIn("Join alerts", labels)
        self.assertIn("OFF", labels)
        self.assertIn("Leave alerts", labels)
        self.assertIn("ON", labels)
        self.assertIn("Disappearing time: /disappear 5sec / 10sec", labels)
        data = _data(markup)
        for expected in ("/settings", "/set track off", "/set join on", "/set leave off", "/set end off", "/help"):
            self.assertIn(expected, data)

    def test_callback_payloads_respect_telegram_byte_limit(self):
        screens = [
            keyboards.home(True, True), keyboards.home(False, True), keyboards.help_screen(),
            keyboards.live(), keyboards.history(), keyboards.stats("weekly", True, True, 1234567890),
            keyboards.top("today", False, True), keyboards.me("all", True, True, 1234567890123),
            keyboards.groups("all"), keyboards.settings({}), keyboards.owner_panel(), keyboards.owner_sub(),
        ]
        for markup in screens:
            for item in _data(markup):
                self.assertLessEqual(len(("nav|" + item).encode()), 64, item)

    def test_add_to_group_link_requests_only_manage_chat(self):
        keyboards.set_bot_username("TestVCBloggerBot")
        try:
            links = [b.url for row in keyboards.home(False).inline_keyboard for b in row if getattr(b, "url", None)]
            self.assertEqual(len(links), 1)
            self.assertIn("startgroup=setup", links[0])
            self.assertIn("admin=manage_chat", links[0])
            self.assertNotIn("delete_messages", links[0])
        finally:
            keyboards.set_bot_username(None)


if __name__ == "__main__":
    unittest.main()
