"""Inline navigation and owner-action buttons.

Normal screen callbacks carry command text (``nav|/top weekly global``). Sensitive owner
actions use separate tokenized callbacks so announcement delivery always needs confirmation.
"""
from typing import Any, Dict, Optional

try:
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:  # keeps the logic importable (and testable) without Pyrogram
    from dataclasses import dataclass

    @dataclass
    class InlineKeyboardButton:
        text: str
        callback_data: str | None = None
        url: str | None = None

    @dataclass
    class InlineKeyboardMarkup:
        inline_keyboard: list

PERIOD_TABS = (("Today", "today"), ("Week", "weekly"), ("Month", "monthly"), ("All time", "all"))
MIN_TIME_CHOICES = (5, 10, 15, 30)

_BOT_USERNAME = ""


def set_bot_username(username: str | None) -> None:
    global _BOT_USERNAME
    _BOT_USERNAME = (username or "").strip().lstrip("@")


def command(name: str, period: str | None = None, *, glob: bool = False,
            owner: int | None = None, extra: str = "") -> str:
    """Build the command text stored in a button (kept well under 64 bytes)."""
    parts = [f"/{name}"]
    if period:
        parts.append(period)
    if glob:
        parts.append("global")
    if extra:
        parts.append(extra)
    if owner:
        parts.append(f"u{int(owner)}")
    return " ".join(parts)


def _button(label: str, text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=f"nav|{text}")


def _home_row(owner: int | None = None):
    return [_button("‹ Back", command("start", owner=owner))]


def _tabs(name: str, current: str, glob: bool, owner: int | None):
    return [
        _button(f"• {label}" if value == current else label, command(name, value, glob=glob, owner=owner))
        for label, value in PERIOD_TABS
    ]


def _scope_button(name: str, period: str, glob: bool, owner: int | None):
    if glob:
        return _button("📍 This group", command(name, period, owner=owner))
    return _button("🌐 All groups", command(name, period, glob=True, owner=owner))


def add_to_group_row():
    if not _BOT_USERNAME:
        return []
    url = f"https://t.me/{_BOT_USERNAME}?startgroup=setup&admin=manage_chat"
    return [[InlineKeyboardButton("➕ Add to group", url=url)]]


def private_stats_link() -> InlineKeyboardMarkup:
    """A deep link that opens personal statistics in the user's private chat."""
    if not _BOT_USERNAME:
        return InlineKeyboardMarkup([[_button("Open private chat", "/start")]])
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "🔒 Open my stats privately", url=f"https://t.me/{_BOT_USERNAME}?start=me"
    )]])


def home(is_group: bool, is_owner: bool = False) -> InlineKeyboardMarkup:
    """Clean, task-first home screen. Commands remain available for power users."""
    if is_group:
        rows = [
            [_button("🎙 Live VC", "/vc"), _button("📊 Group stats", "/stats")],
            [_button("🏆 Top members", "/top"), _button("🌐 Top groups", "/groups")],
            [_button("👤 My stats", "/me"), _button("📖 Help", "/help")],
        ]
    else:
        rows = add_to_group_row() + [
            [_button("🏆 Top members", "/top"), _button("🌐 Top groups", "/groups")],
            [_button("👤 My stats", "/me"), _button("📖 Help", "/help")],
        ]
    if is_owner:
        rows.append([_button("🛠 Owner console", "/owner")])
    return InlineKeyboardMarkup(rows)


def open_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("⌂ Open dashboard", "/start")]])


def help_screen(is_group: bool = True) -> InlineKeyboardMarkup:
    if is_group:
        rows = [
            [_button("🎙 Live VC", "/vc"), _button("📊 Group stats", "/stats")],
            [_button("🏆 Top members", "/top"), _button("🌐 Top groups", "/groups")],
            [_button("⚙️ Settings", "/settings"), _button("⌂ Dashboard", "/start")],
        ]
    else:
        rows = [
            [_button("🏆 Top members", "/top"), _button("🌐 Top groups", "/groups")],
            [_button("👤 My stats", "/me"), _button("⌂ Dashboard", "/start")],
        ]
    return InlineKeyboardMarkup(rows)



def live() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("↻ Refresh", "/vc"), _button("🕘 History", "/history")],
        [_button("⌂ Dashboard", "/start"), _button("📊 Stats", "/stats")],
    ])


def history() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("🎙 Live VC", "/vc"), _button("📊 Stats", "/stats")], [_button("⌂ Dashboard", "/start")]])


def stats(period: str, is_group: bool, glob: bool, owner: int | None = None) -> InlineKeyboardMarkup:
    rows = [_tabs("stats", period, glob, owner)]
    if is_group:
        rows.append([_button("🏆 Top members", command("top", period, glob=glob)), _button("👤 My stats", command("me", period, glob=glob))])
        rows.append([_scope_button("stats", period, glob, owner)])
    else:
        rows.append([_button("🏆 Top members", command("top", period)), _button("🌐 Top groups", command("groups", period))])
    rows.append(_home_row())
    return InlineKeyboardMarkup(rows)


def top(period: str, is_group: bool, glob: bool, owner: int | None = None) -> InlineKeyboardMarkup:
    # Keep leaderboard navigation minimal: period tabs, scope switch, back.
    rows = [_tabs("top", period, glob, owner)]
    if is_group:
        rows.append([_scope_button("top", period, glob, owner)])
    else:
        rows.append([_button("Top groups", command("groups", period))])
    rows.append(_home_row())
    return InlineKeyboardMarkup(rows)


def me(period: str, is_group: bool, glob: bool, owner: int) -> InlineKeyboardMarkup:
    """Personal card: every button is locked to its owner (see router: u<id>)."""
    rows = [_tabs("me", period, glob, owner)]
    if is_group:
        rows.append([_button("🏆 Leaderboard", command("top", period, glob=glob, owner=owner)),
                     _button("📊 Overview", command("stats", period, glob=glob, owner=owner))])
        rows.append([_scope_button("me", period, glob, owner)])
    else:
        rows.append([_button("🏆 Leaderboard", command("top", period, owner=owner))])
    rows.append(_home_row(owner))
    return InlineKeyboardMarkup(rows)


def groups(period: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([_tabs("groups", period, False, None), _home_row()])


def _selected_ttl(cfg: Dict[str, Any]) -> int:
    """Return a valid TTL without treating zero (keep forever) as the default."""
    try:
        value = int(cfg.get("event_message_ttl_seconds", 10))
    except (TypeError, ValueError):
        value = 10
    return value if value in (0, 5, 10, 15, 30) else -1


def settings(cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    def row(label: str, key: str, action: str):
        on = bool(cfg.get(key, True))
        target = "off" if on else "on"
        return [InlineKeyboardButton(label, callback_data="nav|/settings"),
                InlineKeyboardButton("ON" if on else "OFF", callback_data=f"nav|/set {action} {target}")]
    rows = [
        row("Tracking", "logging_enabled", "track"),
        row("Join alerts", "notify_on_join", "join"),
        row("Leave alerts", "notify_on_leave", "leave"),
        row("End summary", "notify_on_end", "end"),
        [InlineKeyboardButton(
            f"{'• ' if _selected_ttl(cfg) == seconds else ''}{label}",
            callback_data=f"nav|/disappear {seconds}sec",
        ) for seconds, label in ((0, "Keep"), (5, "5s"), (10, "10s"))],
        [InlineKeyboardButton(
            f"{'• ' if _selected_ttl(cfg) == seconds else ''}{seconds}s",
            callback_data=f"nav|/disappear {seconds}sec",
        ) for seconds in (15, 30)],
        _home_row(),
    ]
    return InlineKeyboardMarkup(rows)


def broadcast_confirmation(token: str) -> InlineKeyboardMarkup:
    """Confirmation is tokenized and owner-bound by the router/client."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Send to groups", callback_data=f"bc|send|{token}|groups")],
        [InlineKeyboardButton("✅ Groups + users", callback_data=f"bc|send|{token}|all")],
        [InlineKeyboardButton("✖ Cancel", callback_data=f"bc|cancel|{token}")],
    ])


def owner_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("🩺 Health", "/health"), _button("🚨 Issues", "/incidents")],
        [_button("📣 Broadcast", "/broadcast"), _button("📦 Backup now", "/backupnow")],
        [_button("📈 System stats", "/botstats")],
        [_button("⌂ Dashboard", "/start")],
    ])


def owner_sub() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("↻ Refresh", "/health"), _button("🛠 Owner console", "/owner")], [_button("⌂ Dashboard", "/start")]])
