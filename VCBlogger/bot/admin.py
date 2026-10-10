"""Group settings changes (admins only; the permission check lives in the client)."""
from typing import List

from ..database.groups import get_group_settings, update_group_settings

MAX_MIN_SECONDS = 3600

_TOGGLES = {
    "track": ("logging_enabled", "Tracking"),
    "join": ("notify_on_join", "Join alerts"),
    "leave": ("notify_on_leave", "Leave alerts"),
    "end": ("notify_on_end", "End summary"),
}


def normalize_set_args(command: str, args: List[str]) -> List[str]:
    """Translate older typed commands into the single ``set`` form.

    /threshold 10          -> min 10
    /vcnotices join_off    -> join off        /vcnotices on|off -> both
    """
    args = [a.lower() for a in args]
    if command == "threshold":
        return ["min", *args[:1]] if args else []
    if command == "vcnotices" and args:
        word = args[0]
        if word in ("on", "off"):
            return ["notices", word]
        if "_" in word:
            kind, _, state = word.partition("_")
            if kind in ("join", "leave") and state in ("on", "off"):
                return [kind, state]
    return args


async def apply_setting(group_id: int, args: List[str]) -> str:
    """Apply one change and return a short confirmation sentence."""
    if not args:
        return ""
    action = args[0]
    if action == "notices":
        enabled = len(args) > 1 and args[1] == "on"
        await update_group_settings(group_id, {"notify_on_join": enabled, "notify_on_leave": enabled})
        return f"Join and leave alerts: {'ON' if enabled else 'OFF'}."
    if action == "min":
        if len(args) < 2 or not args[1].isdigit() or not 0 <= int(args[1]) <= MAX_MIN_SECONDS:
            return f"Send a number of seconds between 0 and {MAX_MIN_SECONDS}, e.g. /threshold 10."
        await update_group_settings(group_id, {"min_duration": int(args[1])})
        return f"Minimum time: {int(args[1])}s."
    if action in _TOGGLES:
        key, label = _TOGGLES[action]
        if len(args) > 1 and args[1] in ("on", "off"):
            enabled = args[1] == "on"
        else:
            enabled = not bool((await get_group_settings(group_id)).get(key, True))
        await update_group_settings(group_id, {key: enabled})
        return f"{label}: {'ON' if enabled else 'OFF'}."
    return "Unknown setting."
