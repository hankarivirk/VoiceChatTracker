"""Regression tests for settings UX, ephemeral notices, and privacy-safe recovery."""
import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from VCBlogger.bot import keyboards
from VCBlogger.bot.context import Ctx
from VCBlogger.bot.router import parse, render
from VCBlogger.database.groups import get_group_settings
from VCBlogger.vc.recovery import RecoveryEngine
from VCBlogger.vc.sessions import ActiveSession, SessionManager


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_keep_notice_button_does_not_misreport_zero_as_ten_seconds():
    markup = keyboards.settings({"event_message_ttl_seconds": 0})
    labels = [button.text for button in _buttons(markup)]
    callbacks = [button.callback_data for button in _buttons(markup)]
    assert "• Keep" in labels
    assert "• 10s" not in labels
    assert "nav|/disappear 0sec" in callbacks
    assert all(len(value.encode()) <= 64 for value in callbacks if value)


def test_disappear_parser_accepts_keep_mode():
    parsed = parse("/disappear 0sec")
    assert parsed is not None
    assert parsed.name == "disappear"
    assert parsed.args == ("0sec",)
    assert parsed.mutates is True


@pytest.mark.asyncio
async def test_disappear_zero_renders_explicit_keep_message():
    ctx = Ctx(user_id=123, chat_id=-100123456, is_group=True, first_name="Admin", username="", is_owner=True)
    screen = await render("/disappear 0sec", ctx)
    assert screen is not None
    assert "stay in chat history" in screen.text
    assert "• Keep" in [button.text for row in screen.markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_group_default_notice_ttl_uses_environment_setting():
    from VCBlogger.database import groups
    group_id = -100987600123
    with patch.object(groups, "VC_EVENT_MESSAGE_TTL_SECONDS", 23):
        cfg = await get_group_settings(group_id)
    assert cfg["event_message_ttl_seconds"] == 23


@pytest.mark.asyncio
async def test_snapshot_persists_suppressed_presence_state():
    manager = SessionManager()
    group_id, user_id = -100987600124, 987600124
    session = await manager.get_or_create(group_id)
    session.add_participant(user_id, "Privacy Test", "privacy_test")
    manager.suppress_user_for_active_sessions(user_id)

    with patch("VCBlogger.vc.sessions.save_session_snapshot", new_callable=AsyncMock) as save:
        await manager.snapshot_group(group_id)

    save.assert_awaited_once()
    snapshot = save.await_args.args[1]
    assert snapshot["participants"][0]["record_time"] is False
    assert user_id in session.suppressed_user_ids
    # No extra attendance is produced by closing a suppressed session.
    await manager.end_session(group_id)


@pytest.mark.asyncio
async def test_title_metadata_failure_does_not_break_menu_requests():
    from VCBlogger.bot import client as client_module
    chat = SimpleNamespace(id=-100987600125, title="Transient Database Failure")
    client_module._known_titles.pop(chat.id, None)
    with patch("VCBlogger.database.groups.update_group_settings", new_callable=AsyncMock,
               side_effect=ConnectionError("temporary database outage")):
        await client_module._remember_title(chat)  # must be best-effort, not raise
    assert chat.id not in client_module._known_titles


@pytest.mark.asyncio
async def test_recovery_does_not_recreate_deleted_suppressed_user_record():
    engine = RecoveryEngine()
    active_doc = {
        "session_id": "privacy-recovery-test",
        "group_id": -100987600126,
        "start_time": 100,
        "last_snapshot": 130,
        "duration_type": "occupied_presence",
        "occupied_seconds": 20,
        "participants": [{
            "user_id": 987600126,
            "first_name": "Deleted Name",
            "username": "deleted_name",
            "joined_at": 110,
            "duration": 20,
            "segment_id": "privacy-segment",
            "record_time": False,
        }],
        "historical_participants": [],
        "peak_participants": 1,
    }
    with patch("VCBlogger.vc.recovery.get_active_sessions", new_callable=AsyncMock, return_value=[active_doc]), \
         patch("VCBlogger.vc.recovery.get_group_settings", new_callable=AsyncMock, return_value={"min_duration": 5}), \
         patch("VCBlogger.vc.recovery.close_session", new_callable=AsyncMock) as close_session, \
         patch("VCBlogger.vc.recovery.log_incident", new_callable=AsyncMock), \
         patch("VCBlogger.vc.recovery.add_user_voice_time", new_callable=AsyncMock) as add_time, \
         patch("VCBlogger.vc.recovery.mark_user_session", new_callable=AsyncMock) as mark_session:
        recovered = await engine.recover_dangling_sessions()

    assert recovered == 1
    add_time.assert_not_awaited()
    mark_session.assert_not_awaited()
    assert close_session.await_args.args[2] == []


@pytest.mark.asyncio
async def test_suppressed_user_voice_time_is_not_recreated(monkeypatch):
    from VCBlogger.database import users as users_module
    db = AsyncMock()
    monkeypatch.setattr(users_module, "_is_user_suppressed", lambda _uid: True)
    monkeypatch.setattr(users_module, "get_db", db)

    result = await users_module.add_user_voice_time(
        987600127, -100987600127, 60, event_id="suppressed-event"
    )

    assert result is False
    db.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_suppression_covers_session_already_closing():
    import asyncio
    from VCBlogger.vc import sessions as sessions_module

    manager = SessionManager()
    group_id, user_id = -100987600128, 987600128
    session = ActiveSession(group_id, "closing-privacy-test")
    session.add_participant(user_id, "Closing Test", "closing_test")
    manager.sessions[group_id] = session
    reached_settings = asyncio.Event()
    release_settings = asyncio.Event()

    async def pause_settings(_group_id):
        reached_settings.set()
        await release_settings.wait()
        return {"min_duration": 0}

    with patch.object(sessions_module, "get_group_settings", side_effect=pause_settings), \
         patch.object(sessions_module, "add_user_voice_time", new_callable=AsyncMock) as add_time, \
         patch.object(sessions_module, "mark_user_session", new_callable=AsyncMock) as mark_session, \
         patch.object(sessions_module, "close_session", new_callable=AsyncMock):
        closing = asyncio.create_task(manager.end_session(group_id))
        await asyncio.wait_for(reached_settings.wait(), timeout=1)
        assert group_id in manager._ending_sessions
        manager.suppress_user_for_active_sessions(user_id, clear_history=True)
        release_settings.set()
        result = await asyncio.wait_for(closing, timeout=1)

    add_time.assert_not_awaited()
    mark_session.assert_not_awaited()
    assert result["participants"] == []
    assert group_id not in manager._ending_sessions


@pytest.mark.asyncio
async def test_session_counter_recovers_if_process_stops_after_attendance_insert(monkeypatch):
    from VCBlogger.database import users as users_module
    from VCBlogger.database.mongo import get_db

    uid, group_id, session_id = 987600129, -100987600129, "partial-attendance-write-test"
    await users_module.delete_user_data(uid)
    db = await get_db()
    await users_module.get_or_create_user(uid, "Counter Recovery", "counter_recovery")
    user_collection = db["users"]
    original_update = user_collection.update_one

    async def fail_counter_increment(query, update, upsert=False):
        if "sessions_count" in update.get("$inc", {}):
            raise RuntimeError("simulated process interruption before aggregate update")
        return await original_update(query, update, upsert=upsert)

    monkeypatch.setattr(user_collection, "update_one", fail_counter_increment)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        await users_module.mark_user_session(uid, group_id, session_id, "Counter Recovery", "counter_recovery")

    monkeypatch.setattr(user_collection, "update_one", original_update)
    # The attendance row is durable; replaying the event must repair, not double-count.
    assert await users_module.mark_user_session(uid, group_id, session_id, "Counter Recovery", "counter_recovery") is False
    user = await user_collection.find_one({"user_id": uid})
    assert user["sessions_count"] == 1
    assert user["group_sessions"][str(group_id)] == 1
    await users_module.delete_user_data(uid)
