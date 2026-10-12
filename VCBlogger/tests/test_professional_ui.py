"""Regression tests for the v4.3 professional UI, owner tools, and name hygiene."""
import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import pytest

from VCBlogger.bot import keyboards, router, screens
from VCBlogger.bot.context import AccessDenied, Ctx
from VCBlogger.database.mongo import get_db
from VCBlogger.vc.sessions import ActiveSession

OWNER = Ctx(user_id=88001, chat_id=88001, is_group=False, first_name="Owner", is_owner=True)
USER = Ctx(user_id=88002, chat_id=88002, is_group=False, first_name="Member")
GROUP = Ctx(user_id=88002, chat_id=-10088002, is_group=True, first_name="Member")


def all_buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_help_never_lists_owner_only_commands_even_for_owner():
    assert "/broadcast" not in screens.help_text(False, is_owner=True)
    assert "/deletedata" not in screens.help_text(True, is_owner=True)
    assert "/owner" not in screens.help_text(False, is_owner=True)


def test_owner_console_exposes_useful_owner_actions_as_buttons():
    callbacks = [button.callback_data for button in all_buttons(keyboards.owner_panel())]
    assert "nav|/broadcast" in callbacks
    assert "nav|/backupnow" in callbacks
    assert "nav|/botstats" in callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks if value)


def test_broadcast_parser_preserves_original_message_case_and_lines():
    text = "/broadcast Hello TEAM!\nMonthly Report: 100% Ready <do-not-parse>"
    parsed = router.parse(text)
    assert parsed.name == "broadcast"
    assert parsed.args == ("Hello TEAM!\nMonthly Report: 100% Ready <do-not-parse>",)
    assert router.parse("/announce Update is LIVE").args == ("Update is LIVE",)


@pytest.mark.asyncio
async def test_broadcast_preview_is_escaped_and_requires_confirmation():
    preview = await router.render("/broadcast Hi <b>Team</b>!", OWNER)
    assert "BROADCAST PREVIEW" in preview.text
    assert "Hi &lt;b&gt;Team&lt;/b&gt;!" in preview.text
    callbacks = [button.callback_data for button in all_buttons(preview.markup)]
    assert any(value and value.startswith("bc|send|") and value.endswith("|groups") for value in callbacks)
    assert any(value and value.startswith("bc|send|") and value.endswith("|all") for value in callbacks)
    assert any(value and value.startswith("bc|cancel|") for value in callbacks)


@pytest.mark.asyncio
async def test_broadcast_is_owner_only_and_private_only():
    with pytest.raises(AccessDenied):
        await router.render("/broadcast Notice", USER)
    with pytest.raises(AccessDenied):
        await router.render("/broadcast Notice", GROUP)


@pytest.mark.asyncio
async def test_broadcast_target_discovery_uses_known_groups_and_optional_users():
    from VCBlogger.bot.client import _collect_broadcast_targets

    db = await get_db()
    await db["groups"].update_one({"group_id": -10088101}, {"$set": {"group_id": -10088101}}, upsert=True)
    await db["sessions"].insert_one({"session_id": "broadcast-target-session", "group_id": -10088102})
    await db["users"].update_one({"user_id": 88103}, {"$set": {"user_id": 88103}}, upsert=True)

    group_targets = await _collect_broadcast_targets(False, owner_id=88001)
    all_targets = await _collect_broadcast_targets(True, owner_id=88001)
    assert -10088101 in group_targets
    assert -10088102 in group_targets
    assert 88103 not in group_targets
    assert 88103 in all_targets
    assert 88001 not in all_targets


def test_active_participant_name_can_be_enriched_but_not_downgraded():
    session = ActiveSession(-10088201, "name-hygiene")
    session.add_participant(88201, "User_88201", "")
    session.add_participant(88201, "Hushan Virk", "hushan")
    assert session.participants[88201].first_name == "Hushan Virk"
    assert session.participants[88201].username == "hushan"

    session.add_participant(88201, "User_88201", "")
    assert session.participants[88201].first_name == "Hushan Virk"
