"""MTProto listener for Telegram group-call participant events and version-gap recovery.

Bot API alone does not provide the complete group-call participant transition stream.
A logged-in MTProto user session with access to each target group is required.
"""
import asyncio
import time
from typing import Optional, Tuple, Dict, Any

try:
    from pyrogram import Client, raw
    from pyrogram.handlers import RawUpdateHandler
    from pyrogram.utils import get_peer_id
except ImportError:
    class Client:
        pass
    class raw:
        types = type("types", (), {})
        functions = type("functions", (), {})
    class RawUpdateHandler:
        def __init__(self, *args, **kwargs): pass
    def get_peer_id(peer):
        return getattr(peer, "channel_id", getattr(peer, "chat_id", getattr(peer, "user_id", 0)))

from ..config import API_ID, API_HASH, USER_SESSION_STRINGS, VC_STARTUP_DISCOVERY_LIMIT
from ..database.incidents import log_incident
from ..database.groups import is_group_logging_enabled
from ..utils.logging import logger
from ..utils.telegram_compat import is_group_chat, enum_name, is_admin_status
from .monitor import vc_monitor
from .sessions import session_manager

_user_client = None  # compatibility alias for the primary configured Assistant
_user_clients: list[Any] = []
_connected_monitor_clients: dict[int, Any] = {}
_call_clients: dict[int, Any] = {}
_call_to_group: dict[int, int] = {}
_call_objects: dict[int, Any] = {}
_call_versions: dict[int, int] = {}
_participant_updates_seen: set[int] = set()
_last_resync_attempt: dict[int, float] = {}
_last_gap_incident: dict[int, float] = {}
_call_lock: dict[int, asyncio.Lock] = {}
_group_to_call: dict[int, int] = {}
class _IncompleteRosterError(RuntimeError):
    """Telegram returned a partial roster, so treating it as authoritative is unsafe."""


_monitor_state: dict[str, Any] = {
    "connected": False,
    "connected_accounts": 0,
    "connected_user_ids": [],
    "started_at": None,
    "user_id": None,
    "last_raw_update_at": None,
    "last_vc_update_at": None,
    "last_participant_update_at": None,
    "last_reconcile_at": None,
    "participant_updates": 0,
    "participant_events": 0,
    "call_updates": 0,
    "startup_calls_discovered": 0,
    "startup_groups_checked": 0,
    "last_error": None,
}


def set_monitor_connected(connected: bool, client=None) -> None:
    """Record each Assistant lifecycle and maintain aggregate health status."""
    if connected and client is not None:
        me = getattr(client, "me", None)
        account_id = getattr(me, "id", None)
        key = int(account_id) if account_id is not None else id(client)
        _connected_monitor_clients[key] = client
        _monitor_state["started_at"] = _monitor_state.get("started_at") or time.time()
        _monitor_state["user_id"] = account_id
        _monitor_state["last_error"] = None
    elif client is not None:
        me = getattr(client, "me", None)
        account_id = getattr(me, "id", None)
        key = int(account_id) if account_id is not None else id(client)
        # A failed duplicate session must not unregister an already-connected
        # different Client that happens to represent the same account ID.
        if _connected_monitor_clients.get(key) is client:
            _connected_monitor_clients.pop(key, None)
    else:
        _connected_monitor_clients.clear()
        _monitor_state["started_at"] = None
    _monitor_state["connected"] = bool(_connected_monitor_clients)
    _monitor_state["connected_accounts"] = len(_connected_monitor_clients)
    _monitor_state["connected_user_ids"] = [str(x) for x in _connected_monitor_clients.keys()]


def get_monitor_status() -> dict[str, Any]:
    """Return a copy so callers cannot mutate the listener's internal status."""
    return dict(_monitor_state)


def _forget_call(call_id: int) -> None:
    group_id = _call_to_group.pop(int(call_id), None)
    if group_id is not None and _group_to_call.get(group_id) == int(call_id):
        _group_to_call.pop(group_id, None)
    _call_objects.pop(int(call_id), None)
    _call_clients.pop(int(call_id), None)
    _call_versions.pop(int(call_id), None)
    _participant_updates_seen.discard(int(call_id))
    _last_resync_attempt.pop(int(call_id), None)
    _last_gap_incident.pop(int(call_id), None)
    _call_lock.pop(int(call_id), None)


def _peer_chat_id(peer):
    try:
        return int(get_peer_id(peer))
    except Exception:
        if hasattr(peer, "channel_id"):
            return int(f"-100{peer.channel_id}")
        if hasattr(peer, "chat_id"):
            return -int(peer.chat_id)
        return None


def _input_group_call(call):
    """Build the input call object accepted by phone.getGroupCall."""
    if call is None:
        return None
    if call.__class__.__name__ == "InputGroupCall":
        return call
    call_id = getattr(call, "id", None)
    access_hash = getattr(call, "access_hash", None)
    if call_id is None or access_hash is None:
        return None
    try:
        return raw.types.InputGroupCall(id=int(call_id), access_hash=int(access_hash))
    except Exception:
        return None


def _update_group_id(update):
    """Chat id for an UpdateGroupCall across Telegram layers.

    Newer layers expose `peer`; the layer shipped with Pyrogram 2.0.106 exposes a bare
    `chat_id` (a supergroup/channel id, which Bot-API style ids prefix with -100).
    """
    peer = getattr(update, "peer", None)
    if peer is not None:
        return _peer_chat_id(peer)
    bare = getattr(update, "chat_id", None)
    if bare is None:
        return None
    bare = int(bare)
    return bare if bare < 0 else int(f"-100{bare}")


def _user_name(user) -> Tuple[str, str]:
    first = " ".join(part for part in [getattr(user, "first_name", ""), getattr(user, "last_name", "")] if part).strip()
    return first or f"User_{getattr(user, 'id', 'unknown')}", getattr(user, "username", "") or ""


async def _get_authoritative_participants(client, input_call) -> Optional[Tuple[int, list[dict[str, Any]]]]:
    """Fetch the current participant roster and paginate if Telegram returns a partial list."""
    try:
        first_page = await client.invoke(raw.functions.phone.GetGroupCall(call=input_call, limit=100))
        participants = list(getattr(first_page, "participants", None) or [])
        users_by_id = {int(getattr(user, "id", 0)): user for user in (getattr(first_page, "users", None) or [])}
        group_call = getattr(first_page, "call", None)
        participant_count = int(getattr(group_call, "participants_count", len(participants)) or 0)
        listeners_hidden = bool(getattr(group_call, "listeners_hidden", False))
        offset = getattr(first_page, "participants_next_offset", "") or ""
        pages = 0
        seen_offsets = set()
        while offset:
            # Guard against malformed/old API responses repeating offsets forever.
            if offset in seen_offsets or pages >= 100:
                raise _IncompleteRosterError("Telegram returned an incomplete VC participant roster; reconciliation was skipped.")
            seen_offsets.add(offset)
            pages += 1
            page = await client.invoke(raw.functions.phone.GetGroupParticipants(
                call=input_call,
                ids=[],
                sources=[],
                offset=offset,
                limit=100,
            ))
            participants.extend(getattr(page, "participants", None) or [])
            for user in getattr(page, "users", None) or []:
                users_by_id[int(getattr(user, "id", 0))] = user
            next_offset = getattr(page, "next_offset", "") or ""
            if not next_offset:
                offset = ""
                break
            if next_offset == offset:
                raise _IncompleteRosterError("Telegram participant pagination stalled; roster reconciliation was skipped.")
            offset = next_offset
        current_users_by_id: dict[int, dict[str, Any]] = {}
        for participant in participants:
            peer = getattr(participant, "peer", None)
            uid = getattr(peer, "user_id", None)
            if uid is None:
                continue
            uid = int(uid)
            user = users_by_id.get(uid)
            name, username = _user_name(user) if user else (f"User_{uid}", "")
            record = {"user_id": uid, "first_name": name, "username": username}
            # Mic state is deliberately not read or tracked; presence alone counts.
            # phone.getGroupParticipants can overlap its first page; de-duplicate by ID.
            existing = current_users_by_id.get(uid, {})
            if name.startswith("User_") and existing.get("first_name"):
                record["first_name"] = existing["first_name"]
            if not username and existing.get("username"):
                record["username"] = existing["username"]
            current_users_by_id[uid] = record
        current_users = list(current_users_by_id.values())
        if listeners_hidden and participant_count > len(current_users):
            # Telegram explicitly states that hidden listeners cannot be fetched with
            # phone.getGroupParticipants. Treat this as partial data, not authoritative,
            # or a refresh could falsely mark still-present listeners as having left.
            raise _IncompleteRosterError("Telegram hides some voice-chat listeners; complete roster tracking is unavailable for this call.")
        if participant_count > len(current_users):
            raise _IncompleteRosterError(
                f"Telegram returned an incomplete VC participant roster ({len(current_users)}/{participant_count}); reconciliation was skipped."
            )
        version = int(getattr(group_call, "version", getattr(first_page, "version", 0)) or 0)
        return version, current_users
    except _IncompleteRosterError as exc:
        logger.warning("Could not reconcile authoritative VC participants: %s", exc)
        raise
    except Exception as exc:
        logger.warning("Could not refresh authoritative VC participants: %s", exc)
        return None


async def _log_version_gap(group_id: int, call_id: int, current_version: int, incoming_version: int, reason: str):
    stamp = time.monotonic()
    if stamp - _last_gap_incident.get(call_id, 0.0) < 30:
        return
    _last_gap_incident[call_id] = stamp
    try:
        await log_incident(
            group_id=group_id,
            incident_type="PARTICIPANT_UPDATE_GAP",
            details=(f"Voice-chat participant update sequence gap for call {call_id}: "
                     f"cached version {current_version}, incoming version {incoming_version}. "
                     f"{reason} Participant durations may be undercounted around the gap."),
            severity="warning",
        )
    except Exception as exc:
        logger.warning("Could not record VC version-gap incident: %s", exc)


async def _reconcile_call(client, call_id: int, group_id: int, input_call=None, *, force: bool = False) -> bool:
    """Refresh Telegram's authoritative roster and reconcile local membership.

    A successful empty roster still means a call is active, so create a session even
    when nobody is currently connected. Disabled groups are version-tracked but never
    allowed to create attendance sessions.
    """
    now = time.monotonic()
    if not force and now - _last_resync_attempt.get(call_id, 0.0) < 2:
        return False
    _last_resync_attempt[call_id] = now
    input_call = input_call or _call_objects.get(call_id)
    if input_call is None:
        return False
    try:
        # Bound the entire paginated Telegram request chain. A slow API call must not
        # stall update handling indefinitely, especially while a voice chat is active.
        snapshot = await asyncio.wait_for(
            _get_authoritative_participants(client, input_call),
            timeout=20,
        )
    except _IncompleteRosterError as exc:
        _monitor_state["last_error"] = str(exc)[:300]
        return False
    except asyncio.TimeoutError:
        _monitor_state["last_error"] = "Timed out retrieving the authoritative voice-chat participant roster."
        logger.warning("Timed out retrieving VC participants for group %s / call %s", group_id, call_id)
        return False
    if snapshot is None:
        _monitor_state["last_error"] = "Could not retrieve the authoritative participant roster."
        return False
    version, participants = snapshot
    if await is_group_logging_enabled(group_id):
        # Only emit reconciled joins/leaves after the call's first authoritative
        # snapshot. Initial discovery must not announce every already-present member.
        prior_session = session_manager.sessions.get(group_id)
        # A call-level update may be seen while group tracking is disabled. Require
        # an actual seeded session as well, otherwise enabling tracking later would
        # falsely announce every already-present participant as a new join.
        had_baseline = call_id in _participant_updates_seen and prior_session is not None
        prior_ids = set(prior_session.participants) if prior_session is not None else set()
        current_by_id = {int(p["user_id"]): p for p in participants if p.get("user_id") is not None}
        current_ids = set(current_by_id)
        recovered_joins = sorted(current_ids - prior_ids) if had_baseline else []
        recovered_leaves = sorted(prior_ids - current_ids) if had_baseline else []
        # Snapshot display names before reconciliation mutates the active participant map.
        prior_people = {
            uid: person
            for uid, person in (prior_session.participants.items() if prior_session else [])
        }
        await vc_monitor.handle_call_start(group_id)
        await session_manager.reconcile_participants(group_id, participants)
        for uid in recovered_joins:
            person = current_by_id.get(uid, {})
            await vc_monitor.notify_transition(group_id, uid, person.get("first_name", ""), "join")
        for uid in recovered_leaves:
            person = prior_people.get(uid)
            await vc_monitor.notify_transition(
                group_id, uid, getattr(person, "first_name", f"User_{uid}"), "leave"
            )
    else:
        logger.info("Voice-chat roster discovered in group %s, but tracking is disabled there.", group_id)
    _call_versions[call_id] = version
    _participant_updates_seen.add(call_id)
    _monitor_state["last_reconcile_at"] = time.time()
    _monitor_state["last_error"] = None
    logger.info("Reconciled %s VC participants in group %s at call version %s", len(participants), group_id, version)
    return True


async def _process_participant_transitions(update, users, group_id: int) -> None:
    for participant in getattr(update, "participants", []) or []:
        peer = getattr(participant, "peer", None)
        user_id = getattr(peer, "user_id", None)
        if user_id is None:
            continue
        user_id = int(user_id)
        user = users.get(user_id) if isinstance(users, dict) else None
        first_name, username = _user_name(user) if user else (f"User_{user_id}", "")
        is_left = bool(getattr(participant, "left", False))
        just_joined = bool(getattr(participant, "just_joined", False))
        if is_left:
            await vc_monitor.handle_participant_update(group_id, user_id, "leave", first_name, username)
            _monitor_state["last_participant_update_at"] = time.time()
            _monitor_state["participant_events"] += 1
            continue
        if just_joined:
            await vc_monitor.handle_participant_update(group_id, user_id, "join", first_name, username)
            _monitor_state["last_participant_update_at"] = time.time()
            _monitor_state["participant_events"] += 1


async def _raw_update(client, update, users, chats):
    try:
        _monitor_state["last_raw_update_at"] = time.time()
        if isinstance(update, raw.types.UpdateGroupCall):
            _monitor_state["last_vc_update_at"] = time.time()
            _monitor_state["call_updates"] += 1
            group_id = _update_group_id(update)
            call = getattr(update, "call", None)
            call_id = getattr(call, "id", None)
            if group_id is None or call_id is None:
                return
            call_id = int(call_id)
            is_discarded = (
                isinstance(call, getattr(raw.types, "GroupCallDiscarded", ()))
                or bool(getattr(call, "discarded", False))
                or call.__class__.__name__ == "GroupCallDiscarded"
            )
            if is_discarded:
                # A delayed discard for an earlier call must not close a newer session
                # in the same group (the session store is keyed by group ID).
                current_call_id = _group_to_call.get(group_id)
                if current_call_id in (None, call_id):
                    await vc_monitor.handle_call_end(group_id)
                _forget_call(call_id)
                return

            was_known = _call_to_group.get(call_id) == group_id
            previous_call_id = _group_to_call.get(group_id)
            if previous_call_id is not None and previous_call_id != call_id:
                logger.warning(
                    "A new Telegram voice chat was detected in group %s before the prior call closed; closing the stale tracker.",
                    group_id,
                )
                await vc_monitor.handle_call_end(group_id)
                _forget_call(previous_call_id)
                was_known = False

            _call_to_group[call_id] = group_id
            _group_to_call[group_id] = call_id
            _call_clients[call_id] = client
            input_call = _input_group_call(call)
            if input_call is not None:
                _call_objects[call_id] = input_call
            if not was_known:
                # The authoritative refresh creates the session and includes anyone
                # already speaking. If it fails, keep the current call mapped so the
                # incremental updates can still be processed best-effort.
                success = await _reconcile_call(client, call_id, group_id, input_call) if input_call is not None else False
                if not success:
                    if await is_group_logging_enabled(group_id):
                        await vc_monitor.handle_call_start(group_id)
                    logger.warning("Initial VC participant snapshot unavailable for group %s; awaiting participant updates.", group_id)
            else:
                incoming_version = getattr(call, "version", None)
                current_version = _call_versions.get(call_id)
                if incoming_version is not None and current_version is not None and int(incoming_version) > current_version + 1:
                    success = await _reconcile_call(client, call_id, group_id, input_call)
                    if not success:
                        await _log_version_gap(group_id, call_id, current_version, int(incoming_version), "Could not refresh the current roster.")
            return

        if not isinstance(update, raw.types.UpdateGroupCallParticipants):
            return
        _monitor_state["last_vc_update_at"] = time.time()
        _monitor_state["participant_updates"] += 1
        call_id = int(getattr(getattr(update, "call", None), "id", 0) or 0)
        group_id = _call_to_group.get(call_id)
        if group_id is None:
            logger.debug("Ignoring VC participant update until its call-to-group mapping is known.")
            return
        if _group_to_call.get(group_id, call_id) != call_id:
            logger.debug("Ignoring stale participant update for replaced call %s in group %s.", call_id, group_id)
            return
        input_call = _input_group_call(getattr(update, "call", None)) or _call_objects.get(call_id)
        incoming_version_value = getattr(update, "version", None)
        incoming_version = int(incoming_version_value) if incoming_version_value is not None else None
        current_version = _call_versions.get(call_id)

        lock = _call_lock.setdefault(call_id, asyncio.Lock())
        async with lock:
            # A current roster fetch is required to close a gap; never pretend an
            # out-of-order participant update was a complete history.
            if incoming_version is not None and current_version is not None:
                if incoming_version < current_version:
                    logger.debug("Ignoring stale VC update version %s (cached %s)", incoming_version, current_version)
                    return
                if incoming_version == current_version and call_id in _participant_updates_seen:
                    return
                if incoming_version > current_version + 1:
                    await _log_version_gap(group_id, call_id, current_version, incoming_version, "Attempting authoritative roster reconciliation.")
                    if await _reconcile_call(client, call_id, group_id, input_call):
                        return
                    # Best-effort process visible transitions while leaving the old
                    # version untouched so later updates trigger another resync attempt.
                    await _process_participant_transitions(update, users, group_id)
                    return
            elif incoming_version is not None and current_version is None:
                if await _reconcile_call(client, call_id, group_id, input_call):
                    return

            await _process_participant_transitions(update, users, group_id)
            if incoming_version is not None:
                _call_versions[call_id] = incoming_version
                _participant_updates_seen.add(call_id)
    except Exception as exc:
        _monitor_state["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        logger.exception("Failed processing Telegram VC raw update")


async def discover_active_group_calls(client, max_groups: int | None = None) -> int:
    """At startup, find ongoing calls in the MTProto account's accessible dialogs.

    Telegram does not replay every call-start event after a process restart. Looking at
    each accessible group's full info lets us recover an already-active call and seed
    its current participant roster instead of silently waiting for the next VC.
    """
    max_groups = max_groups or VC_STARTUP_DISCOVERY_LIMIT
    max_groups = max(1, min(int(max_groups), 500))
    dialog_limit = min(2000, max(100, max_groups * 5))
    groups = []
    try:
        async for dialog in client.get_dialogs(limit=dialog_limit):
            chat = getattr(dialog, "chat", None)
            if chat is None or not is_group_chat(chat):
                continue
            groups.append(chat)
            if len(groups) >= max_groups:
                break
    except Exception as exc:
        logger.warning("Could not enumerate all MTProto group dialogs for VC recovery: %s", exc)
        _monitor_state["last_error"] = f"Startup dialog discovery: {type(exc).__name__}: {exc}"[:300]

    semaphore = asyncio.Semaphore(4)
    found = 0

    async def inspect_group(chat):
        nonlocal found
        async with semaphore:
            group_id = int(chat.id)
            try:
                title = getattr(chat, "title", None)
                if title:
                    from ..database.groups import get_group_settings, update_group_settings
                    stored = await get_group_settings(group_id)
                    if stored.get("title") != title:
                        await update_group_settings(group_id, {"title": title})
                async def fetch_full_info():
                    peer = await client.resolve_peer(group_id)
                    if isinstance(peer, getattr(raw.types, "InputPeerChannel", ())):
                        return await client.invoke(raw.functions.channels.GetFullChannel(channel=peer))
                    if isinstance(peer, getattr(raw.types, "InputPeerChat", ())):
                        return await client.invoke(raw.functions.messages.GetFullChat(chat_id=int(peer.chat_id)))
                    return None

                full_info = await asyncio.wait_for(fetch_full_info(), timeout=10)
                full_chat = getattr(full_info, "full_chat", None) if full_info is not None else None
                call = getattr(full_chat, "call", None) if full_chat is not None else None
                if call is None or bool(getattr(call, "discarded", False)):
                    return
                call_id = getattr(call, "id", None)
                input_call = _input_group_call(call)
                if call_id is None or input_call is None:
                    return
                call_id = int(call_id)
                current_call_id = _group_to_call.get(group_id)
                if current_call_id is not None and current_call_id != call_id:
                    await vc_monitor.handle_call_end(group_id)
                    _forget_call(current_call_id)
                _call_to_group[call_id] = group_id
                _group_to_call[group_id] = call_id
                _call_objects[call_id] = input_call
                _call_clients[call_id] = client
                # Only count a call as recovered after Telegram returns a valid roster.
                # A discovery scan must be bounded so one inaccessible or slow call
                # cannot hold the entire bot startup indefinitely.
                try:
                    reconciled = await asyncio.wait_for(
                        _reconcile_call(client, call_id, group_id, input_call, force=True),
                        timeout=25,
                    )
                except asyncio.TimeoutError:
                    _monitor_state["last_error"] = "Timed out while recovering an active voice-chat roster."
                    reconciled = False
                if reconciled:
                    found += 1
                    logger.info("Recovered ongoing voice chat in %s (%s).", chat.title or group_id, group_id)
                else:
                    # Preserve the call mapping even when Telegram hides/fails to return
                    # the initial roster. Future join/leave updates can still be tracked.
                    if await is_group_logging_enabled(group_id):
                        await vc_monitor.handle_call_start(group_id)
                    logger.warning("Active voice chat in %s (%s) was found, but the initial roster could not be reconciled; incremental updates remain enabled.", chat.title or group_id, group_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("No recoverable active VC in group %s: %s", group_id, exc)

    await asyncio.gather(*(inspect_group(chat) for chat in groups), return_exceptions=True)
    _monitor_state["startup_calls_discovered"] = found
    _monitor_state["startup_groups_checked"] = len(groups)
    logger.info("Startup VC discovery checked %s group(s) and recovered %s active call(s).", len(groups), found)
    return found


async def reconcile_active_group_calls(client=None) -> int:
    """Reconcile calls, failing over to another connected Assistant if needed."""
    clients = list(_user_clients)
    if not clients or not _group_to_call:
        return 0
    connected = []
    registered_clients = list(_connected_monitor_clients.values())
    for candidate in clients:
        is_registered = any(candidate is item for item in registered_clients)
        is_connected = getattr(candidate, "is_connected", True)
        if is_registered and is_connected:
            connected.append(candidate)
    if not connected:
        return 0
    semaphore = asyncio.Semaphore(4)

    async def reconcile_one(group_id: int, call_id: int) -> bool:
        owner = _call_clients.get(call_id)
        if owner not in connected:
            # The original Assistant may have disconnected. Reassign this call to a
            # healthy configured account so the next reconciliation can recover it.
            owner = connected[0]
            _call_clients[call_id] = owner
        selected_client = owner
        if selected_client is None:
            return False
        if client is not None and selected_client is not client:
            return False
        async with semaphore:
            if _group_to_call.get(group_id) != call_id:
                return False
            input_call = _call_objects.get(call_id)
            if input_call is None:
                return False
            lock = _call_lock.setdefault(call_id, asyncio.Lock())
            try:
                async with lock:
                    if _group_to_call.get(group_id) != call_id:
                        return False
                    return await _reconcile_call(selected_client, call_id, group_id, input_call, force=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _monitor_state["last_error"] = f"Periodic VC reconcile: {type(exc).__name__}: {exc}"[:300]
                logger.warning("Periodic VC reconciliation failed for group %s / call %s: %s", group_id, call_id, exc)
                return False

    tasks = [reconcile_one(group_id, call_id) for group_id, call_id in list(_group_to_call.items())]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    _monitor_state["last_reconcile_at"] = time.time()
    return sum(result is True for result in results)


def get_user_monitors():
    """Build one raw-update monitor per configured user session (three maximum)."""
    global _user_client, _user_clients
    if _user_clients:
        return list(_user_clients)
    if not USER_SESSION_STRINGS:
        logger.warning("No MTProto Assistant sessions configured; returning an empty monitor list. Bot API commands can still run.")
        return []
    clients = []
    for index, session_string in enumerate(USER_SESSION_STRINGS, start=1):
        client = Client(
            f"VCBloggerAssistant{index}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True,
        )
        client.add_handler(RawUpdateHandler(_raw_update))
        clients.append(client)
    _user_clients = clients
    _user_client = clients[0] if clients else None
    return list(_user_clients)


def get_user_monitor():
    """Compatibility helper returning the first configured Assistant client."""
    clients = get_user_monitors()
    return clients[0] if clients else None


async def ensure_assistants_in_group(bot_client, group_id: int) -> dict[str, Any]:
    """Try to invite configured Assistant user accounts when the bot has permission.

    A Telegram bot cannot directly add another account; it can create an invite link
    only when it has the invite-users right, and each logged-in Assistant joins that link.
    Failures are returned for a clear manual instruction instead of being hidden.
    """
    clients = list(_connected_monitor_clients.values())
    ids = [int(getattr(getattr(client, "me", None), "id", 0) or 0) for client in clients]
    ids = [uid for uid in ids if uid > 0]
    if not clients:
        return {"ok": False, "reason": "no_connected_assistant", "ids": ids, "joined": []}
    try:
        bot_me = getattr(bot_client, "me", None)
        if bot_me is None:
            try:
                bot_me = await bot_client.get_me()
            except Exception:
                pass
        bot_id = getattr(bot_me, "id", None)
        if bot_id is None:
            return {"ok": False, "reason": "bot_identity_unavailable", "ids": ids, "joined": []}
        member = await bot_client.get_chat_member(int(group_id), int(bot_id))
        is_adm = is_admin_status(getattr(member, "status", None))
        status_name = enum_name(getattr(member, "status", ""))
        is_owner = status_name in ("owner", "creator")
        privileges = getattr(member, "privileges", None)
        can_invite = is_owner or bool(getattr(privileges, "can_invite_users", False))
        if not is_adm:
            return {"ok": False, "reason": "bot_not_admin", "ids": ids, "joined": []}
        if not can_invite:
            return {"ok": False, "reason": "no_invite_permission", "ids": ids, "joined": []}
        invite = await bot_client.create_chat_invite_link(int(group_id), name="VCBlogger Assistant setup")
        invite_url = getattr(invite, "invite_link", None)
        if not invite_url:
            return {"ok": False, "reason": "invite_link_unavailable", "ids": ids, "joined": []}
        joined = []
        failed = []
        for assistant in clients:
            uid = int(getattr(getattr(assistant, "me", None), "id", 0) or 0)
            if not uid:
                continue
            already_in = False
            try:
                member_status = await assistant.get_chat_member(int(group_id), uid)
                m_stat = enum_name(getattr(member_status, "status", ""))
                if m_stat and m_stat not in ("banned", "kicked", "left"):
                    already_in = True
            except Exception:
                pass
            if already_in:
                joined.append(uid)
                continue
            try:
                await assistant.join_chat(invite_url)
                joined.append(uid)
            except Exception as exc:
                if "ALREADY_PARTICIPANT" in str(exc).upper():
                    joined.append(uid)
                else:
                    logger.warning("Could not auto-join Assistant %s to group %s: %s", uid, group_id, exc)
                    failed.append(uid)
        try:
            await bot_client.revoke_chat_invite_link(int(group_id), invite_url)
        except Exception:
            pass
        return {"ok": not failed and bool(joined), "reason": "join_failed" if failed else "ok", "ids": ids, "joined": joined, "failed": failed}
    except Exception as exc:
        logger.warning("Assistant auto-setup could not run for group %s: %s", group_id, exc)
        return {"ok": False, "reason": "permission_or_api_error", "ids": ids, "joined": []}
