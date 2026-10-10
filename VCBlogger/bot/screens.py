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
PERIOD_HINT = "Use <code>today</code>, <code>weekly</code> or <code>all</code>, e.g. <code>/top weekly</code>."


def _scope(group_id: Optional[int]) -> str:
    return "This group" if group_id is not None else "All groups"


def _line(label: str, value: Any) -> str:
    return f"{label}: <b>{value}</b>"


def _nothing_yet(what: str, period: str) -> str:
    when = {"today": " today", "weekly": " this week"}.get(period, " yet")
    return f"No {what} recorded{when}."


def usage() -> str:
    return PERIOD_HINT


def welcome() -> str:
    return "<b>VCBlogger ready</b>\nVC attendance and rankings."


def help_text(is_group: bool, is_owner: bool = False) -> str:
    if is_group:
        body = (
            "/vc — live voice chat\n"
            "/stats — group stats\n"
            "/top — top users\n"
            "/groups — top groups\n"
            "/me or /mystats — your stats\n"
            "/history — recent VC history\n"
            "/settings — settings (admins)\n"
            "/disappear 10sec — notice expiry (admins)"
        )
    else:
        body = (
            "/stats — all-groups stats\n"
            "/top — top users\n"
            "/groups — top groups\n"
            "/me or /mystats — your stats\n"
            "/help — command list"
        )
    if is_owner:
        body += "\n/owner — owner panel\n/health — bot health\n/deletedata — clear all data (confirm required)\n/deleteuserdata USER_ID — delete user data\n/removeuser USER_ID — block user\n/unremoveuser USER_ID — unblock user"
    return f"<b>Commands</b>\n{body}\n\nPeriods: <code>today</code>, <code>weekly</code>, <code>all</code>."


async def home(ctx: Ctx) -> str:
    if not ctx.is_group:
        return (
            "<b>VCBlogger</b>\n"
            "See voice chat time across your groups.\n\n"
            "Add me to a group to start tracking."
        )
    active = session_manager.sessions.get(ctx.chat_id)
    if active:
        now = f"🎙 Live · {len(active.participants)} in call · {format_duration(active.current_duration())}"
    else:
        now = "No voice chat right now"
    return f"<b>VCBlogger</b>\nVoice chat attendance for this group.\n\n{now}"


async def live(chat_id: int) -> str:
    active = session_manager.sessions.get(chat_id)
    if not active:
        return "<b>Live voice chat</b>\nNo voice chat is running in this group."
    roster = sorted(active.get_all_roster(), key=lambda p: -int(p.get("duration", 0) or 0))
    lines = [
        "🎙 <b>Live voice chat</b>",
        f"{format_duration(active.current_duration())} · {len(roster)} in call · peak {active.peak_participants}",
    ]
    if not roster:
        lines += ["", "Nobody is in the call yet."]
    else:
        lines.append("")
        for index, person in enumerate(roster[:LIVE_LIST_LIMIT], 1):
            who = person_link(person.get("user_id"), person.get("first_name"), person.get("username"))
            lines.append(f"{index}. {who} — {format_duration(person.get('duration', 0))}")
        if len(roster) > LIVE_LIST_LIMIT:
            lines.append(f"…and {len(roster) - LIVE_LIST_LIMIT} more")
    return "\n".join(lines)


async def history(chat_id: int) -> str:
    sessions = await get_recent_sessions(group_id=chat_id, limit=8)
    lines = ["<b>Recent voice chats</b>"]
    active = session_manager.sessions.get(int(chat_id))
    if active:
        lines += [f"\n🟢 LIVE · {format_duration(active.current_duration())} · {len(active.participants)} present"]
    if sessions:
        lines.append("")
        for session in sessions:
            lines.append(
                f"{format_when(session.get('start_time'))} — "
                f"{format_duration(session.get('duration', 0))} · peak {int(session.get('peak_participants', 0) or 0)}"
            )
    elif not active:
        lines.append("\nNo voice chats recorded yet.")
    return "\n".join(lines)


async def stats(group_id: Optional[int], period: str) -> str:
    summary = await get_period_group_summary(group_id, period)
    title = "Group stats" if group_id is not None else "All groups"
    head = f"<b>{title}</b> · {period_label(period)}"
    if not summary["total_sessions"] and not summary["unique_users_tracked"]:
        return f"{head}\n\n{_nothing_yet('voice chats', period)}"
    return "\n".join([
        head,
        "",
        _line("Voice chats", summary["total_sessions"]),
        _line("Call time", summary["formatted_total_duration"]),
        _line("Member time", summary["formatted_participant_duration"]),
        _line("Members", summary["unique_users_tracked"]),
        _line("Peak in a call", summary["peak_participants"]),
        _line("Average call", summary["formatted_average_session"]),
    ])


async def top(group_id: Optional[int], period: str) -> str:
    users = await get_period_user_totals(group_id, period, limit=10000)
    head = f"<b>Leaderboard</b> · {_scope(group_id)} · {period_label(period)}"
    if not users:
        return f"{head}\n\n{_nothing_yet('voice time', period)}"
    lines = [head, ""]
    for rank, user in enumerate(users[:TOP_LIMIT], 1):
        who = person_link(user.get("user_id"), user.get("first_name"), user.get("username"))
        seconds = int(user.get("score", user.get("total_duration", 0)) or 0)
        badge = MEDALS.get(rank, f"{rank}.")
        lines.append(f"{badge} {who} — {format_duration(seconds)}")
    if len(users) > TOP_LIMIT:
        lines += ["", f"Top {TOP_LIMIT} of {len(users)}"]
    return "\n".join(lines)


async def me(ctx: Ctx, group_id: Optional[int], period: str) -> str:
    data = await get_period_user_stats(ctx.user_id, group_id, period)
    head = f"<b>Your stats</b> · {_scope(group_id)} · {period_label(period)}"
    who = person_link(ctx.user_id, ctx.first_name, ctx.username)
    if not int(data.get("total_duration", 0) or 0):
        return f"{head}\n{who}\n\n{_nothing_yet('voice time', period)}"
    rank, ranked = await get_user_rank(ctx.user_id, group_id, period)
    lines = [
        head, who, "",
        _line("Voice time", data["formatted_duration"]),
        _line("Calls joined", data["sessions_count"]),
        _line("Average per call", data["formatted_average"]),
    ]
    if rank:
        lines.append(_line("Rank", f"#{rank} of {ranked}"))
    if period == "all":
        lines.append(_line("Tier", compute_user_tier(int(data.get("total_duration", 0) or 0))))
    return "\n".join(lines)


async def groups(period: str) -> str:
    rows = await get_global_group_breakdown(period, limit=TOP_LIMIT)
    head = f"<b>Top groups</b> · {period_label(period)}"
    if not rows:
        return f"{head}\n\n{_nothing_yet('finished voice chats', period)}"
    lines = [head, ""]
    for rank, row in enumerate(rows, 1):
        title = str(row.get("title") or "").strip()
        # Old records may have stored the numeric id as the title: never show ids.
        if not title or re.fullmatch(r"group[_ ]?-?\d+", title, flags=re.IGNORECASE):
            title = "Unnamed group"
        badge = MEDALS.get(rank, f"{rank}.")
        lines.append(
            f"{badge} {escape_html(title)} — {row['formatted_vc_time']} · "
            f"{row['sessions']} calls · peak {row['peak']}"
        )
    return "\n".join(lines)


async def settings(chat_id: int) -> tuple[str, Dict[str, Any]]:
    cfg = await get_group_settings(chat_id)
    text = (
        "<b>Group settings</b>\n\n"
        "Tracking — record voice chat time\n"
        "Join / Leave alerts — short notice in this group\n"
        "End summary — recap when a call ends\n"
        f"Disappearing time is set with /disappear 5sec or /disappear 10sec.\n\n"
        "Admins only."
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
    provider = escape_html(select_db_provider() or "none")
    return "\n".join([
        f"<b>Owner panel</b> · v{escape_html(APP_VERSION)}",
        "",
        _line("Database", f"{'OK' if database_ok else 'DOWN'} ({provider})"),
        _line("Assistants", f"{int(monitor.get('connected_accounts', 0) or 0)} of {len(USER_SESSION_STRINGS)} connected"),
        _line("Live calls", len(session_manager.sessions)),
        _line("Last error", escape_html(str(monitor.get("last_error") or "none"))),
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
        f"<b>Health</b> · v{escape_html(APP_VERSION)}", "",
        _line("Database", db_value),
    ]
    if REDIS_URL and selected != "redis":
        lines.append(_line("Redis", "OK" if await ping_redis() else "DOWN"))
    if POSTGRES_ARCHIVE_URL and selected != "postgres":
        lines.append(_line("PostgreSQL", "OK" if await ping_postgres() else "DOWN"))
    backup_state = "ok" if backup.get("last_backup_success") else "not confirmed"
    lines += [
        _line("Assistants", f"{int(monitor.get('connected_accounts', 0) or 0)} connected · IDs {', '.join(str(x) for x in (monitor.get('connected_user_ids', []) or [])) or 'none'}"),
        _line("Live calls", len(session_manager.sessions)),
        _line("Last VC update", _ago(monitor.get("last_vc_update_at"))),
        _line("Last roster check", f"{_ago(monitor.get('last_reconcile_at'))} (every {VC_RECONCILE_INTERVAL_SECONDS}s)"),
        _line("Updates", f"{int(monitor.get('participant_updates', 0) or 0)} received · {int(monitor.get('participant_events', 0) or 0)} processed"),
        _line("Last backup", f"{_utc(backup.get('last_backup_at'))} ({backup_state})"),
        _line("Last error", escape_html(str(monitor.get("last_error") or "none"))),
    ]
    return "\n".join(lines)


async def incidents(group_id: Optional[int]) -> str:
    rows = await get_recent_incidents(group_id=group_id, limit=5)
    head = "<b>Recent issues</b>" + (" · This group" if group_id is not None else "")
    if not rows:
        return f"{head}\n\nNo issues recorded."
    lines = [head, ""]
    for row in rows:
        details = str(row.get("details", ""))
        details = details if len(details) <= 90 else details[:87] + "…"
        lines.append(f"{format_when(row.get('timestamp'))} · {escape_html(row.get('type', 'issue'))} — {escape_html(details)}")
    return "\n".join(lines)
