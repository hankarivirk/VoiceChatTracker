"""Command parsing, access rules and screen rendering.

Pure bot logic: nothing here touches Telegram objects, so every screen can be tested
without a network. The Telegram layer (client.py) only builds a Ctx and sends the Screen.
"""
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ..stats.period_stats import parse_period
from ..utils.formatting import fit_message
from . import keyboards, screens
from .admin import apply_setting, normalize_set_args
from .context import AccessDenied, Ctx

# Canonical commands -> what they show. Older command names keep working as hidden aliases:
# alias -> (canonical command, force the all-groups scope)
ALIASES = {
    "menu": ("start", False), "dashboard": ("start", False),
    "status": ("vc", False), "vcmenu": ("vc", False),
    "sessions": ("history", False),
    "statsmenu": ("stats", False), "groupinfo": ("stats", False), "group": ("stats", False),
    "globalstats": ("stats", True), "globalstatsmenu": ("stats", True),
    "leaderboard": ("top", False), "vctop": ("top", True), "globaltop": ("top", True),
    "mystats": ("me", False), "myrank": ("me", False), "vcstats": ("me", True),
    "vcgroupstats": ("groups", True),
    "settings": ("settings", False), "threshold": ("set", False), "vcnotices": ("set", False),
    "ping": ("health", False), "dbstatus": ("health", False),
}
CANONICAL = {
    "start", "help", "vc", "history", "stats", "top", "me", "groups",
    "settings", "set", "disappear", "owner", "health", "incidents", "deletedata", "deleteuserdata", "removeuser", "unremoveuser",
}
ALL_COMMANDS = sorted(CANONICAL | set(ALIASES))

PERIOD_COMMANDS = {"stats", "top", "me", "groups"}
GROUP_ONLY = {"vc", "history", "settings", "set", "disappear"}
OWNER_ONLY = {"owner", "health", "incidents", "deletedata", "deleteuserdata", "removeuser", "unremoveuser"}
# Pressed from a shared message these must not change what everyone else sees.
PERSONAL = {"me"}
_DELETE_CONFIRMATIONS: dict[int, tuple[str, float]] = {}


@dataclass(frozen=True)
class Parsed:
    name: str
    period: str = "all"
    global_scope: bool = False
    owner_id: Optional[int] = None
    args: Tuple[str, ...] = ()
    bad_period: bool = False

    @property
    def mutates(self) -> bool:
        """True when this command changes a group setting (admin check needed)."""
        return self.name in {"set", "disappear"} and bool(self.args)


@dataclass
class Screen:
    text: str
    markup: Any = None
    notice: str = ""


def parse(text: str) -> Optional[Parsed]:
    """'/top@MyBot weekly global u123' -> Parsed(...); None for foreign commands."""
    tokens = (text or "").strip().split()
    if not tokens:
        return None
    raw = tokens[0].split("@", 1)[0].lstrip("/").lower()
    name, force_global = ALIASES.get(raw, (raw, False))
    if name not in CANONICAL:
        return None
    period, global_scope, owner_id, rest, bad = "all", force_global, None, [], False
    for token in tokens[1:]:
        low = token.lower()
        if low in ("global", "g"):
            global_scope = True
        elif re.fullmatch(r"u\d{1,15}", low):
            owner_id = int(low[1:])
        elif name in PERIOD_COMMANDS and parse_period(low):
            period = parse_period(low)
        elif name in PERIOD_COMMANDS:
            bad = True
        else:
            rest.append(low)
    if name == "set":
        rest = normalize_set_args(raw, rest)
    return Parsed(name, period, global_scope, owner_id, tuple(rest), bad)


def is_foreign_card(parsed: Parsed, user_id: int) -> bool:
    """True when a button belongs to a personal card of somebody else."""
    return bool(parsed.owner_id) and parsed.owner_id != int(user_id)


def needs_own_message(parsed: Parsed, ctx: Ctx) -> bool:
    """'My stats' pressed on a shared group message must be sent as a new message."""
    return ctx.is_group and parsed.name in PERSONAL and not parsed.owner_id


def check_access(parsed: Parsed, ctx: Ctx) -> None:
    if parsed.name in OWNER_ONLY and not ctx.is_owner:
        raise AccessDenied("This is available to the bot owner only.")
    if parsed.name in GROUP_ONLY and not ctx.is_group:
        raise AccessDenied("Use this command inside the group.")


async def render(text: str, ctx: Ctx) -> Optional[Screen]:
    """Build the screen for a command text. Returns None for commands that are not ours."""
    parsed = parse(text)
    if parsed is None:
        return None
    check_access(parsed, ctx)
    name = parsed.name
    is_global = parsed.global_scope or not ctx.is_group
    group_id = None if is_global else ctx.chat_id
    period = parsed.period

    if name in PERIOD_COMMANDS and parsed.bad_period:
        return Screen(screens.usage())

    if name == "start":
        return _screen(await screens.home(ctx), keyboards.home(ctx.is_group, ctx.is_owner))
    if name == "help":
        return _screen(screens.help_text(ctx.is_group, ctx.is_owner), keyboards.help_screen())
    if name == "vc":
        return _screen(await screens.live(ctx.chat_id), keyboards.live())
    if name == "history":
        return _screen(await screens.history(ctx.chat_id), keyboards.history())
    if name == "stats":
        return _screen(await screens.stats(group_id, period), keyboards.stats(period, ctx.is_group, is_global))
    if name == "top":
        return _screen(await screens.top(group_id, period), keyboards.top(period, ctx.is_group, is_global))
    if name == "me":
        return _screen(await screens.me(ctx, group_id, period), keyboards.me(period, ctx.is_group, is_global, ctx.user_id))
    if name == "groups":
        return _screen(await screens.groups(period), keyboards.groups(period))
    if name in ("settings", "set"):
        notice = await apply_setting(ctx.chat_id, list(parsed.args)) if parsed.mutates else ""
        body, cfg = await screens.settings(ctx.chat_id)
        result = _screen(body, keyboards.settings(cfg))
        result.notice = notice
        return result
    if name == "disappear":
        # /disappear 5sec|10sec|15sec|30sec (group admins only)
        if not ctx.is_group:
            raise AccessDenied("Use /disappear inside a group.")
        if not parsed.args or parsed.args[0] not in ("5sec", "10sec", "15sec", "30sec"):
            return Screen("Use /disappear 5sec, /disappear 10sec, /disappear 15sec or /disappear 30sec.")
        seconds = int(parsed.args[0][:-3])
        await __import__("VCBlogger.database.groups", fromlist=["update_group_settings"]).update_group_settings(ctx.chat_id, {"event_message_ttl_seconds": seconds})
        return Screen(f"Disappearing time: {seconds}s.", keyboards.settings(await screens.settings(ctx.chat_id))[1])
    if name in ("deletedata", "deleteuserdata", "removeuser", "unremoveuser"):
        from ..database.mongo import get_db
        from ..database.users import delete_user_data
        db = await get_db()
        if name == "deletedata":
            if not parsed.args:
                token = secrets.token_hex(3)
                _DELETE_CONFIRMATIONS[int(ctx.user_id)] = (token, time.time() + 60)
                return Screen(f"<b>Delete all tracked data?</b>\nThis cannot be undone. Confirm within 60 seconds: <code>/deletedata {token}</code>")
            pending = _DELETE_CONFIRMATIONS.get(int(ctx.user_id))
            supplied = parsed.args[0]
            if not pending or time.time() > pending[1] or not secrets.compare_digest(supplied, pending[0]):
                _DELETE_CONFIRMATIONS.pop(int(ctx.user_id), None)
                return Screen("Confirmation expired or invalid. Run /deletedata to request a new token.")
            _DELETE_CONFIRMATIONS.pop(int(ctx.user_id), None)
            names = ("users", "voice_time_segments", "attendance", "sessions", "groups", "incidents", "open_mic_time_segments", "blocked_users", "maintenance_state")
            failed_collections = []
            for collection in names:
                try:
                    await db[collection].delete_many({})
                except Exception:
                    failed_collections.append(collection)
            from ..vc.sessions import session_manager
            session_manager.sessions.clear()
            archive_note = ""
            try:
                from ..database.postgres_archive import clear_archive_data, is_configured
                if is_configured() and not await clear_archive_data():
                    archive_note = " Optional PostgreSQL archive could not be cleared."
            except Exception:
                archive_note = " Optional PostgreSQL archive could not be cleared."
            failure_note = (" Could not clear: " + ", ".join(failed_collections) + ".") if failed_collections else ""
            return Screen(("Tracked database data deletion finished." if failed_collections else "All tracked database data was deleted.") + failure_note + archive_note + " Backup files are unchanged.")
        if not parsed.args or not parsed.args[0].isdigit():
            return Screen(f"Usage: /{name} USER_ID" + (" confirm" if name == "deleteuserdata" else ""))
        uid = int(parsed.args[0])
        if name == "deleteuserdata":
            if len(parsed.args) < 2 or parsed.args[1] != "confirm":
                return Screen(f"Delete user {uid} data?\nRun <code>/deleteuserdata {uid} confirm</code> to confirm.")
            await delete_user_data(uid)
            from ..vc.sessions import session_manager
            session_manager.suppress_user_for_active_sessions(uid, clear_history=True)
            return Screen(f"Deleted tracked data for user <code>{uid}</code>. Current-call time will not be re-added.")
        if name == "removeuser":
            await db["blocked_users"].update_one({"user_id": uid}, {"$set": {"user_id": uid, "blocked": True}}, upsert=True)
            return Screen(f"User <code>{uid}</code> blocked from tracking.")
        await db["blocked_users"].delete_one({"user_id": uid})
        from ..vc.sessions import session_manager
        session_manager.unsuppress_user_for_active_sessions(uid)
        return Screen(f"User <code>{uid}</code> unblocked.")
    if name == "owner":
        return _screen(await screens.owner_panel(), keyboards.owner_panel())
    if name == "health":
        return _screen(await screens.health(), keyboards.owner_sub("health"))
    if name == "incidents":
        return _screen(await screens.incidents(group_id if ctx.is_group else None), keyboards.owner_sub("incidents"))
    return None


def _screen(text: str, markup: Any) -> Screen:
    return Screen(fit_message(text), markup)
