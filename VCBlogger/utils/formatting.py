"""Text helpers shared by every screen: escaping, durations, names, dates."""
from datetime import datetime
from html import escape as _html_escape
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import STATS_TIMEZONE

# Telegram rejects messages above 4096 characters; stay safely below that.
MAX_MESSAGE_CHARS = 3900


def escape_html(value: Any) -> str:
    """Escape untrusted Telegram names/details for HTML parse mode."""
    return _html_escape(str(value), quote=True)


def format_duration(seconds: float | int) -> str:
    """Seconds -> '1d 2h', '1h 1m 5s', '45s' (zero units are skipped)."""
    total = int(max(0, seconds))
    if total == 0:
        return "0s"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _timezone():
    try:
        return ZoneInfo(STATS_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def format_when(timestamp: Any) -> str:
    """Local date and time, e.g. '10 Oct, 14:02'."""
    if not timestamp:
        return "-"
    try:
        ts = int(float(timestamp))
        return datetime.fromtimestamp(ts, tz=_timezone()).strftime("%d %b, %H:%M").lstrip("0")
    except (ValueError, TypeError, OSError):
        return "-"


def person_name(first_name: Any = "", username: Any = "") -> str:
    """A readable name; never exposes placeholder names like 'User_123'."""
    raw = str(first_name or "").strip()
    if not raw or raw.startswith(("User_", "User ")):
        return f"@{username}" if username else "Unknown user"
    return raw


def person_link(user_id: Any, first_name: Any = "", username: Any = "") -> str:
    """HTML name that opens the Telegram profile when the user id is valid."""
    name = escape_html(person_name(first_name, username))
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return name
    return f'<a href="tg://user?id={uid}">{name}</a>' if uid > 0 else name


def fit_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Trim at a line boundary so one long roster can never break a reply."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > 0 else limit].rstrip() + "\n…"


def format_session_summary(session: Dict[str, Any]) -> str:
    """Short recap posted when a voice chat ends."""
    people = [p for p in session.get("participants", []) if int(p.get("duration", 0) or 0) > 0]
    people.sort(key=lambda p: int(p.get("duration", 0) or 0), reverse=True)
    lines = [
        "<b>Voice chat ended</b>",
        f"{format_duration(session.get('duration', 0))} · "
        f"{len(session.get('participants', []))} members · peak {int(session.get('peak_participants', 0) or 0)}",
    ]
    medals = ("🥇", "🥈", "🥉")
    if people:
        lines.append("")
        for index, person in enumerate(people[:3]):
            who = person_link(person.get("user_id"), person.get("first_name"), person.get("username"))
            lines.append(f"{medals[index]} {who} — {format_duration(person.get('duration', 0))}")
    return "\n".join(lines)


def format_incident_alert(incident_type: str, group_id: int, details: str) -> str:
    """Short owner alert for a tracking problem."""
    return (
        "<b>Tracking issue</b>\n"
        f"{escape_html(incident_type)} · group <code>{int(group_id)}</code>\n"
        f"<i>{escape_html(details)}</i>"
    )
