"""Screen texts. One function per screen, short lines, no filler.

Style: a bold title (with the period/scope after a dot), then plain 'Label: value' lines.
Emojis are used only for rank medals, live status and ON/OFF markers.
"""
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..config import APP_VERSION
from ..database.groups import get_group_settings
from ..database.incidents import get_recent_incidents
from ..database.sessions import get_recent_sessions
from ..stats.period_stats import (
    get_global_group_breakdown, get_period_group_summary, get_period_user_stats,
    get_period_user_totals, get_user_rank, period_label,
)
from ..stats.user_stats import compute_user_tier
from ..utils.formatting import (
    escape_html, format_duration, format_when, person_link,
)
from ..vc.sessions import session_manager
from .context import Ctx

LIVE_LIST_LIMIT = 10
TOP_LIMIT = 10
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
PERIOD_HINT = (
    "Choose a period with the buttons below, or use "
    "<code>today</code>, <code>weekly</code>, <code>monthly</code> or <code>all</code>. "
    "Example: <code>/top monthly</code>."
)


def _scope(group_id: Optional[int]) -> str:
    return "This group" if group_id is not None else "All groups"


def _line(label: str, value: Any) -> str:
    return f"{label}: <b>{value}</b>"


def _nothing_yet(what: str, period: str) -> str:
    when = {"today": " today", "weekly": " this week", "monthly": " this month"}.get(period, " yet")
    return f"No {what} recorded{when}."


def usage() -> str:
    return f"<b>📊 Choose a reporting period</b>\n\n{PERIOD_HINT}"


def welcome() -> str:
    return (
        "<b>🎙 VCBLOGGER</b>\n"
        "<i>Voice chat analytics · Attendance · Rankings</i>\n\n"
        "Your bot is online. Use the dashboard below to explore its tools."
    )


def help_text(is_group: bool, is_owner: bool = False) -> str:
    """Public help only; owner operations live inside the private owner console."""
    if is_group:
        body = (
            "<b>🎙 VOICE CHAT</b>\n"
            "<code>/vc</code> — live participants and time in call\n"
            "<code>/history</code> — recent voice chats\n"
            "<code>/stats</code> — call and attendance summary\n\n"
            "<b>🏆 RANKINGS</b>\n"
            "<code>/top</code> — top members by voice time\n"
            "<code>/groups</code> — groups ranked by active VC time\n"
            "<code>/me</code> — your personal statistics\n\n"
            "<b>⚙️ GROUP CONTROLS</b>\n"
            "<code>/settings</code> — tracking and notification toggles\n"
            "<code>/disappear 10sec</code> — notice lifetime (admins)"
        )
    else:
        body = (
            "<b>🏆 RANKINGS</b>\n"
            "<code>/top</code> — top members across groups\n"
            "<code>/groups</code> — most active groups\n"
            "<code>/me</code> — your personal statistics\n"
            "<code>/stats</code> — overall voice chat summary"
        )
    return (
        "<b>📘 VCBLOGGER · COMMAND GUIDE</b>\n"
        "<i>Tap a button below for the most-used screens.</i>\n\n"
        f"{body}\n\n"
        "<b>REPORTING PERIODS</b>\n"
        "<code>today</code> · <code>weekly</code> · <code>monthly</code> · <code>all</code>\n\n"
        "<i>Personal statistics are delivered privately.\n"
        "Live VC tracking requires a logged-in Assistant account in the group.</i>"
    )


async def home(ctx: Ctx) -> str:
    if not ctx.is_group:
        return (
            "<b>🎙 VCBLOGGER</b>\n"
            "<i>Voice chat attendance and analytics</i>\n\n"
            "Explore member rankings, group activity and your personal stats.\n\n"
            "<b>GET STARTED</b>\n"
            "Add the bot to a group, then make sure a logged-in Assistant account "
            "is also a member of that group for live participant tracking."
        )
    active = session_manager.sessions.get(ctx.chat_id)
    if active:
        status = (
            "🟢 <b>VOICE CHAT LIVE</b>\n"
            f"{len(active.participants)} participants · peak {active.peak_participants}\n"
            f"<i>Current call time: {format_duration(active.current_duration())}</i>"
        )
    else:
        status = "⚪ <b>NO ACTIVE VOICE CHAT</b>\n<i>Live presence will appear here when a call is detected.</i>"
    return (
        "<b>🎙 VCBLOGGER</b>\n"
        "<i>Voice chat attendance · Group dashboard</i>\n\n"
        f"{status}\n\n"
        "<i>Choose a tool below. Screens update in place when possible.</i>"
    )


async def live(chat_id: int) -> str:
    active = session_manager.sessions.get(int(chat_id))
    if not active:
        return (
            "<b>🎙 LIVE VOICE CHAT</b>\n"
            "<i>Status for this group</i>\n\n"
            "⚪ No active voice chat is being tracked right now.\n\n"
            "<i>If a call is already running, check that an Assistant user account is "
            "connected and has joined this group, then use Refresh.</i>"
        )
    roster = sorted(active.get_all_roster(), key=lambda person: -int(person.get("duration", 0) or 0))
    lines = [
        "<b>🎙 LIVE VOICE CHAT</b>",
        f"🟢 <b>{len(roster)} in call</b> · {format_duration(active.current_duration())} · peak {active.peak_participants}",
        "",
    ]
    if not roster:
        lines.append("<i>The call is active, but no participants are visible yet.</i>")
    else:
        for index, person in enumerate(roster[:LIVE_LIST_LIMIT], 1):
            who = person_link(person.get("user_id"), person.get("first_name"), person.get("username"))
            lines.append(f"<b>{index:02d}</b>  {who}  <i>· {format_duration(person.get('duration', 0))}</i>")
        if len(roster) > LIVE_LIST_LIMIT:
            lines += ["", f"<i>…and {len(roster) - LIVE_LIST_LIMIT} more participants.</i>"]
    return "\n".join(lines)


async def history(chat_id: int) -> str:
    sessions = await get_recent_sessions(group_id=chat_id, limit=8)
    lines = ["<b>🕘 VOICE CHAT HISTORY</b>", "<i>Most recent tracked calls</i>"]
    active = session_manager.sessions.get(int(chat_id))
    if active:
        lines += ["", f"🟢 <b>LIVE</b> · {format_duration(active.current_duration())} · {len(active.participants)} present"]
    if sessions:
        lines.append("")
        for index, session in enumerate(sessions, 1):
            lines.append(
                f"<b>{index:02d}</b>  {format_when(session.get('start_time'))} · "
                f"<b>{format_duration(session.get('duration', 0))}</b> · "
                f"peak {int(session.get('peak_participants', 0) or 0)}"
            )
    elif not active:
        lines += ["", "<i>No completed voice chats have been recorded yet.</i>"]
    return "\n".join(lines)


async def stats(group_id: Optional[int], period: str) -> str:
    summary = await get_period_group_summary(group_id, period)
    period_name = period_label(period)
    if group_id is not None:
        try:
            cfg = await get_group_settings(int(group_id))
            scope_name = str(cfg.get("title") or "This group").strip()
            if (not scope_name or scope_name.casefold() in {"unnamed group", "unknown group", "none"}
                    or re.fullmatch(r"group[_ ]?-?\d+", scope_name, flags=re.IGNORECASE)):
                scope_name = "This group"
            scope_name = escape_html(scope_name)
        except Exception:
            scope_name = "This group"
        title = f"<b>📊 GROUP OVERVIEW</b>\n<i>{scope_name} · {escape_html(period_name)}</i>"
    else:
        title = f"<b>🌐 All groups overview</b>\n<i>All groups · {escape_html(period_name)}</i>"
    if not summary["total_sessions"] and not summary["unique_users_tracked"]:
        return f"{title}\n\n<i>{escape_html(_nothing_yet('voice chats', period))}</i>"
    return "\n".join([
        title,
        "",
        _line("Tracked calls", summary["total_sessions"]),
        _line("Occupied VC time", summary["formatted_total_duration"]),
        _line("Combined member time", summary["formatted_participant_duration"]),
        _line("Members tracked", summary["unique_users_tracked"]),
        _line("Peak participants", summary["peak_participants"]),
        _line("Average call", summary["formatted_average_session"]),
    ])


async def top(group_id: Optional[int], period: str) -> str:
    users = await get_period_user_totals(group_id, period, limit=10000)
    head = f"<b>🏆 MEMBER LEADERBOARD</b>\n<i>{escape_html(_scope(group_id))} · {escape_html(period_label(period))}</i>"
    if not users:
        return f"{head}\n\n<i>{escape_html(_nothing_yet('voice time', period))}</i>"
    lines = [head, ""]
    for rank, user in enumerate(users[:TOP_LIMIT], 1):
        who = person_link(user.get("user_id"), user.get("first_name"), user.get("username"))
        seconds = int(user.get("score", user.get("total_duration", 0)) or 0)
        badge = MEDALS.get(rank, f"<b>{rank:02d}.</b>")
        lines.append(f"{badge}  {who}  <i>· {format_duration(seconds)}</i>")
    if len(users) > TOP_LIMIT:
        lines += ["", f"<i>Top {TOP_LIMIT} of {len(users)} members</i>"]
    return "\n".join(lines)


async def me(ctx: Ctx, group_id: Optional[int], period: str) -> str:
    data = await get_period_user_stats(ctx.user_id, group_id, period)
    head = f"<b>👤 YOUR VOICE STATS</b>\n<i>{escape_html(_scope(group_id))} · {escape_html(period_label(period))}</i>"
    who = person_link(ctx.user_id, ctx.first_name, ctx.username)
    if not int(data.get("total_duration", 0) or 0):
        return f"{head}\n{who}\n\n<i>{escape_html(_nothing_yet('voice time', period))}</i>"
    rank, ranked = await get_user_rank(ctx.user_id, group_id, period)
    lines = [
        head, who, "",
        _line("Voice time", data["formatted_duration"]),
        _line("Calls attended", data["sessions_count"]),
        _line("Average per call", data["formatted_average"]),
    ]
    if rank:
        lines.append(_line("Rank", f"#{rank} of {ranked}"))
    if period == "all":
        lines.append(_line("Tier", compute_user_tier(int(data["total_duration"]))))
    return "\n".join(lines)


async def groups(period: str) -> str:
    rows = await get_global_group_breakdown(period, limit=TOP_LIMIT)
    head = f"<b>🌐 MOST ACTIVE GROUPS</b>\n<i>{escape_html(period_label(period))} · occupied voice-chat time</i>"
    if not rows:
        return f"{head}\n\n<i>{escape_html(_nothing_yet('finished voice chats', period))}</i>"
    lines = [head, ""]
    for rank, row in enumerate(rows, 1):
        title = str(row.get("title") or "").strip()
        # Old records may have stored the numeric id as the title: never show ids.
        if (not title or title.casefold() in {"unnamed group", "unknown group", "none"}
                or re.fullmatch(r"group[_ ]?-?\d+", title, flags=re.IGNORECASE)):
            title = "Group title unavailable"
        badge = MEDALS.get(rank, f"<b>{rank:02d}.</b>")
        lines.append(
            f"{badge}  <b>{escape_html(title)}</b>\n"
            f"    <i>{row['formatted_vc_time']} · {row['sessions']} calls · peak {row['peak']}</i>"
        )
    return "\n".join(lines)


def _ttl_label(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 10
    if seconds <= 0:
        return "Keep messages"
    return f"{min(seconds, 3600)}s"


async def settings(chat_id: int) -> tuple[str, Dict[str, Any]]:
    cfg = await get_group_settings(chat_id)
    def state(key: str) -> str:
        return "🟢 Enabled" if cfg.get(key, True) else "⚪ Disabled"
    text = (
        "<b>⚙️ GROUP CONTROL CENTER</b>\n"
        "<i>Changes apply only to this group.</i>\n\n"
        f"<b>Voice tracking</b> · {state('logging_enabled')}\n"
        f"<b>Join alerts</b> · {state('notify_on_join')}\n"
        f"<b>Leave alerts</b> · {state('notify_on_leave')}\n"
        f"<b>Call-end summary</b> · {state('notify_on_end')}\n"
        f"<b>Notice expiry</b> · {_ttl_label(cfg.get('event_message_ttl_seconds', 10))}\n\n"
        "<i>Tap ON/OFF to change a setting. Choose Keep, 5s, 10s, 15s or 30s for notices.\n"
        "Only group admins and the bot owner can change these controls.</i>"
    )
    return text, cfg


# ---------- owner screens ----------

def _ago(timestamp: Any) -> str:
    try:
        return f"{format_duration(max(0, time.time() - float(timestamp)))} ago" if timestamp else "never"
    except (TypeError, ValueError):
        return "unknown"


def _utc(timestamp: Any) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%d %b %H:%M UTC") if timestamp else "never"
    except (TypeError, ValueError, OSError):
        return "unknown"


async def owner_panel() -> str:
    from ..config import USER_SESSION_STRINGS, select_db_provider
    from ..database.mongo import ping as ping_primary
    from ..vc.telegram_monitor import get_monitor_status
    monitor = get_monitor_status()
    try:
        database_ok = await ping_primary()
    except Exception:
        database_ok = False
    provider = escape_html(select_db_provider() or "not configured")
    assistant_count = int(monitor.get("connected_accounts", 0) or 0)
    assistant_state = f"🟢 {assistant_count} connected" if assistant_count else "🔴 none connected"
    last_vc = _ago(monitor.get("last_vc_update_at"))
    return "\n".join([
        f"<b>🛠 Owner panel</b> · VCBLOGGER\n<i>Release {escape_html(APP_VERSION)}</i>",
        "",
        f"<b>Primary database</b> · {'🟢 Online' if database_ok else '🔴 Offline'}",
        f"<b>Provider</b> · {provider}",
        f"<b>Assistants</b> · {assistant_count} of {max(len(USER_SESSION_STRINGS), assistant_count)} connected",
        f"<b>Live calls</b> · {len(session_manager.sessions)}",
        f"<b>Last VC signal</b> · {escape_html(last_vc)}",
        f"<b>Last issue</b> · <i>{escape_html(str(monitor.get('last_error') or 'None'))}</i>",
        "",
        ("⚠️ <i>Live participant tracking is offline. Configure SESSION_STRING or ASSISTANT_SESSION_STRINGS and make sure at least one Assistant account has joined each group.</i>"
         if assistant_count == 0 else "🟢 <i>Assistant listener connected. Only groups accessible to that account can be tracked.</i>"),
        "<i>Use the controls below for delivery, backups, diagnostics and system totals.</i>",
    ])


async def bot_stats() -> str:
    from ..database.mongo import get_db
    db = await get_db()
    async def count(collection: str) -> int:
        return int(await db[collection].count_documents({}))
    groups_count, users_count, sessions_count, incidents_count, segments_count = await __import__(
        "asyncio"
    ).gather(
        count("groups"), count("users"), count("sessions"), count("incidents"), count("voice_time_segments")
    )
    active_count = await db["sessions"].count_documents({"is_active": True})
    return "\n".join([
        "<b>📈 SYSTEM TOTALS</b>",
        "<i>Stored records in the selected primary database</i>",
        "",
        _line("Known groups", groups_count),
        _line("Tracked users", users_count),
        _line("Voice chat sessions", sessions_count),
        _line("Currently active sessions", active_count),
        _line("Attendance segments", segments_count),
        _line("Recorded issues", incidents_count),
        "",
        "<i>Totals include historical records; they are not unique active Telegram users.</i>",
    ])


async def health() -> str:
    from ..config import (
        POSTGRES_ARCHIVE_URL, REDIS_URL, VC_RECONCILE_INTERVAL_SECONDS, select_db_provider,
    )
    from ..database.mongo import get_db, ping as ping_primary
    from ..database.maintenance import storage_usage_bytes
    from ..database.postgres_archive import ping as ping_postgres
    from ..database.redis_cache import ping as ping_redis
    from ..vc.telegram_monitor import get_monitor_status

    monitor = get_monitor_status()
    selected = select_db_provider() or "none"
    database_ok = await ping_primary()
    usage = await storage_usage_bytes() if database_ok else None
    backup: Dict[str, Any] = {}
    if database_ok:
        try:
            backup = (await (await get_db())["maintenance_state"].find_one({"_id": "backup_status"})) or {}
        except Exception:
            backup = {}

    db_value = f"{'OK' if database_ok else 'DOWN'} ({escape_html(selected)})"
    if usage is not None:
        db_value += f" · ~{usage / (1024 * 1024):.1f} MB"
    lines = [
        f"<b>🩺 Health · System diagnostics</b>\n<i>Release {escape_html(APP_VERSION)}</i>", "",
        _line("Database", db_value),
    ]
    if REDIS_URL and selected != "redis":
        lines.append(_line("Redis", "OK" if await ping_redis() else "DOWN"))
    if POSTGRES_ARCHIVE_URL and selected != "postgres":
        lines.append(_line("PostgreSQL", "OK" if await ping_postgres() else "DOWN"))
    backup_state = "ok" if backup.get("last_backup_success") else "not confirmed"
    connected_assistants = int(monitor.get("connected_accounts", 0) or 0)
    lines += [
        _line("Assistants", f"{connected_assistants} connected · IDs {', '.join(monitor.get('connected_user_ids', []) or []) or 'none'}"),
        ("⚠️ <i>VC participant tracking is unavailable until an Assistant user session connects and joins the target group.</i>"
         if connected_assistants == 0 else "🟢 <i>Assistant event listener is connected.</i>"),
        _line("Live calls", len(session_manager.sessions)),
        _line("Last VC update", _ago(monitor.get("last_vc_update_at"))),
        _line("Last roster check", f"{_ago(monitor.get('last_reconcile_at'))} (every {VC_RECONCILE_INTERVAL_SECONDS}s)"),
        _line("Updates", f"{int(monitor.get('participant_updates', 0) or 0)} received · {int(monitor.get('participant_events', 0) or 0)} processed"),
        _line("Last backup", f"{_utc(backup.get('last_backup_at'))} ({backup_state})"),
        _line("Last error", escape_html(str(monitor.get("last_error") or "None"))),
    ]
    return "\n".join(lines)


async def incidents(group_id: Optional[int]) -> str:
    rows = await get_recent_incidents(group_id=group_id, limit=5)
    head = "<b>🚨 Recent issues · Tracking diagnostics</b>" + ("\n<i>This group</i>" if group_id is not None else "\n<i>All tracked groups</i>")
    if not rows:
        return f"{head}\n\n<i>No issues recorded.</i>"
    lines = [head, ""]
    for row in rows:
        details = str(row.get("details", ""))
        details = details if len(details) <= 90 else details[:87] + "…"
        lines.append(f"{format_when(row.get('timestamp'))} · {escape_html(row.get('type', 'issue'))} — {escape_html(details)}")
    return "\n".join(lines)
