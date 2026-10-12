"""Telegram wiring only: receive commands/buttons, call the router, send the result."""
import asyncio
import time
from ..config import BOT_TOKEN, API_ID, API_HASH, SUDO_USERS
from ..utils.logging import logger
from ..utils.telegram_compat import html_parse_mode, is_group_chat
from .context import AccessDenied, Ctx

_bot_client = None
_known_titles: dict[int, str] = {}
_rank_refresh_tasks: dict[tuple[int, int], asyncio.Task] = {}
_assistant_setup_tasks: set[asyncio.Task] = set()

ERROR_TEXT = "Something went wrong. Please try again in a moment."


def _context(chat, sender) -> Ctx:
    return Ctx(
        user_id=int(sender.id),
        chat_id=int(chat.id),
        is_group=is_group_chat(chat),
        first_name=sender.first_name or "",
        username=sender.username or "",
        is_owner=int(sender.id) in SUDO_USERS,
    )


def _is_not_modified(exc: Exception) -> bool:
    return type(exc).__name__ == "MessageNotModified" or "MESSAGE_NOT_MODIFIED" in str(exc).upper()


async def _remember_title(chat) -> None:
    """Store the group name once (not on every command) so rankings show real names."""
    title = getattr(chat, "title", None)
    if not title or _known_titles.get(int(chat.id)) == title:
        return
    from ..database.groups import update_group_settings
    try:
        await update_group_settings(int(chat.id), {"title": title})
    except Exception as exc:
        # A title is display metadata, not a prerequisite for /start, /help or stats.
        # Keep commands responsive during transient database issues and retry later.
        logger.warning("Could not refresh title for chat %s; commands will continue: %s", chat.id, exc)
        return
    _known_titles[int(chat.id)] = title


async def _require_admin(client, ctx: Ctx, parsed) -> None:
    if parsed.mutates and not ctx.is_owner:
        from ..utils.permissions import is_admin
        if not await is_admin(client, ctx.chat_id, ctx.user_id):
            raise AccessDenied("Only group admins can change settings.")


async def _cancel_rank_refresh(chat_id: int, message_id: int) -> None:
    task = _rank_refresh_tasks.pop((int(chat_id), int(message_id)), None)
    if task and not task.done():
        task.cancel()


async def _start_rank_refresh(message, text: str, ctx: Ctx) -> None:
    """Refresh open leaderboard/history screens every 15 seconds, retrying transient failures."""
    parsed = __import__("VCBlogger.bot.router", fromlist=["parse"]).parse(text)
    key = (int(message.chat.id), int(message.id))
    await _cancel_rank_refresh(*key)
    if not parsed or parsed.name not in {"top", "groups", "history"}:
        return

    async def refresh_loop():
        from .router import render
        consecutive_errors = 0
        try:
            while True:
                await asyncio.sleep(15)
                try:
                    screen = await render(text, ctx)
                    if screen is None:
                        return
                    await message.edit_text(
                        screen.text, parse_mode=html_parse_mode(), disable_web_page_preview=True,
                        reply_markup=screen.markup,
                    )
                    consecutive_errors = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if _is_not_modified(exc):
                        consecutive_errors = 0
                        continue
                    consecutive_errors += 1
                    logger.warning(
                        "Leaderboard refresh failed (%s/5) for %s: %s",
                        consecutive_errors, key, exc,
                    )
                    # Retry short network/API failures; stop after repeated permanent
                    # errors (e.g. message deleted or bot lost access).
                    if consecutive_errors >= 5:
                        return
        except asyncio.CancelledError:
            raise
        finally:
            if _rank_refresh_tasks.get(key) is asyncio.current_task():
                _rank_refresh_tasks.pop(key, None)

    _rank_refresh_tasks[key] = asyncio.create_task(refresh_loop(), name=f"rank-refresh-{key[0]}-{key[1]}")


async def _collect_broadcast_targets(include_users: bool, owner_id: int) -> list[int]:
    """Find known chat IDs. User delivery is attempted only after a separate confirmation."""
    from ..database.mongo import get_db
    db = await get_db()
    groups: set[int] = set()
    for collection in ("groups", "sessions"):
        async for record in db[collection].find({}):
            try:
                chat_id = int(record.get("group_id", 0) or 0)
            except (TypeError, ValueError):
                continue
            # Telegram group/supergroup/channel IDs are negative; don't mistake user
            # records or malformed legacy rows for groups.
            if chat_id < 0:
                groups.add(chat_id)
    targets = set(groups)
    if include_users:
        async for record in db["users"].find({}):
            try:
                user_id = int(record.get("user_id", 0) or 0)
            except (TypeError, ValueError):
                continue
            if user_id > 0 and user_id != int(owner_id):
                targets.add(user_id)
    return sorted(targets)


async def _send_broadcast(client, callback, token: str, mode: str) -> None:
    """Confirm and deliver an owner announcement with rate limiting and a result report."""
    from ..config import SUDO_USERS
    from ..utils.formatting import escape_html
    from ..bot import router, keyboards

    sender = getattr(callback, "from_user", None)
    message = getattr(callback, "message", None)
    if sender is None or message is None or int(sender.id) not in SUDO_USERS:
        await callback.answer("Owner access required.", show_alert=True)
        return
    owner_id = int(sender.id)
    pending = router._BROADCAST_CONFIRMATIONS.get(owner_id)
    token_matches = bool(pending and pending[0] == token)
    is_expired = bool(token_matches and time.time() > pending[2])
    if not token_matches or is_expired:
        # A stale button from an older preview must not erase a newer pending preview.
        if token_matches:
            router._BROADCAST_CONFIRMATIONS.pop(owner_id, None)
        await callback.answer("This preview expired. Create a new broadcast.", show_alert=True)
        try:
            await message.edit_text(
                "<b>⌛ Broadcast preview expired.</b>\nOpen the owner console and prepare it again.",
                parse_mode=html_parse_mode(), reply_markup=keyboards.owner_panel(),
            )
        except Exception:
            pass
        return
    if mode == "cancel":
        router._BROADCAST_CONFIRMATIONS.pop(owner_id, None)
        await callback.answer("Broadcast cancelled.")
        await message.edit_text(
            "<b>✖ BROADCAST CANCELLED</b>\n\nNothing was sent.",
            parse_mode=html_parse_mode(), reply_markup=keyboards.owner_panel(),
        )
        return
    if mode not in {"groups", "all"}:
        await callback.answer("Invalid broadcast action.", show_alert=True)
        return

    announcement = pending[1]
    router._BROADCAST_CONFIRMATIONS.pop(owner_id, None)
    await callback.answer("Broadcast started. Results will appear here.")
    try:
        targets = await _collect_broadcast_targets(include_users=(mode == "all"), owner_id=owner_id)
    except Exception:
        logger.exception("Could not collect broadcast targets for owner %s", owner_id)
        await message.edit_text(
            "<b>🔴 BROADCAST FAILED</b>\n\nCould not read recipients from the primary database.",
            parse_mode=html_parse_mode(), reply_markup=keyboards.owner_panel(),
        )
        return

    if not targets:
        await message.edit_text(
            "<b>📣 NO BROADCAST RECIPIENTS</b>\n\nNo known groups were found in the database.",
            parse_mode=html_parse_mode(), reply_markup=keyboards.owner_panel(),
        )
        return

    broadcast_text = f"<b>📣 VCBLOGGER ANNOUNCEMENT</b>\n\n{escape_html(announcement)}"
    delivered = 0
    failed = 0
    skipped = 0
    for target_id in targets:
        sent = False
        for attempt in range(2):
            try:
                await client.send_message(
                    target_id, broadcast_text, parse_mode=html_parse_mode(),
                    disable_web_page_preview=True,
                )
                sent = True
                delivered += 1
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                wait_value = getattr(exc, "value", getattr(exc, "x", None))
                if attempt == 0 and "floodwait" in type(exc).__name__.lower() and wait_value is not None:
                    await asyncio.sleep(min(max(1, int(wait_value)) + 1, 60))
                    continue
                failed += 1
                if any(term in type(exc).__name__.lower() for term in ("peeridinvalid", "userisblocked", "chatwriteforbidden", "botkicked")):
                    skipped += 1
                logger.debug("Broadcast delivery failed for chat %s: %s", target_id, exc)
                break
        # Stay under common Telegram bot broadcast limits; FloodWait is still handled.
        await asyncio.sleep(0.08)

    recipient_scope = "groups + tracked users" if mode == "all" else "groups"
    await message.edit_text(
        "<b>✅ BROADCAST FINISHED</b>\n"
        f"<i>Scope: {escape_html(recipient_scope)}</i>\n\n"
        f"<b>Delivered:</b> {delivered}\n"
        f"<b>Failed:</b> {failed}\n"
        f"<b>Likely unavailable chats:</b> {skipped}\n"
        f"<b>Targets:</b> {len(targets)}\n\n"
        "<i>Private delivery can fail when a user has never started the bot, blocked it, or deleted the account.</i>",
        parse_mode=html_parse_mode(), reply_markup=keyboards.owner_panel(),
    )


def get_bot():
    global _bot_client
    if _bot_client is not None:
        return _bot_client
    try:
        from pyrogram import Client, filters
        from .router import ALL_COMMANDS, is_foreign_card, needs_own_message, parse, render
        from . import keyboards

        _bot_client = Client("VCBloggerBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

        @_bot_client.on_message(filters.command(ALL_COMMANDS))
        async def _on_command(client, message):
            sender = message.from_user
            if not sender:
                return
            ctx = _context(message.chat, sender)
            text = message.text or message.caption or ""
            try:
                if ctx.is_group:
                    await _remember_title(message.chat)
                parsed = parse(text)
                if parsed is None:
                    return
                # Deep-link start payloads can open personal stats without publishing
                # them to the group that supplied the link.
                render_text = "/me" if (parsed.name == "start" and parsed.args and parsed.args[0] == "me" and not ctx.is_group) else text
                await _require_admin(client, ctx, parsed)
                if ctx.is_group and parsed.name == "me":
                    from . import keyboards
                    sent = await message.reply_text(
                        "🔒 Personal stats are private. Open the bot in a private chat to view yours.",
                        reply_markup=keyboards.private_stats_link(),
                        disable_web_page_preview=True,
                    )
                    return
                screen = await render(render_text, ctx)
            except AccessDenied as denied:
                await message.reply_text(denied.message)
                return
            except Exception:
                logger.exception("Command %r failed in chat %s", text[:40], ctx.chat_id)
                await message.reply_text(ERROR_TEXT)
                return
            if screen is None:
                return
            body = f"{screen.notice}\n\n{screen.text}" if screen.notice else screen.text
            sent = await message.reply_text(
                body, parse_mode=html_parse_mode(), disable_web_page_preview=True, reply_markup=screen.markup,
            )
            await _start_rank_refresh(sent, text, ctx)

        @_bot_client.on_callback_query(filters.regex(r"^nav\|"))
        async def _on_button(client, callback):
            message, sender = callback.message, callback.from_user
            if not message or not sender:
                await callback.answer("This menu is no longer available.", show_alert=True)
                return
            text = (callback.data or "").split("|", 1)[1].strip()
            parsed = parse(text)
            if parsed is None:
                await callback.answer("This button is outdated. Send /start.", show_alert=True)
                return
            # Do not cancel a shared screen's refresh when another user clicks a
            # personal button or a stale owner-locked card.
            if is_foreign_card(parsed, sender.id):
                await callback.answer("This card is someone else's. Open your own stats.", show_alert=True)
                return
            ctx = _context(message.chat, sender)
            try:
                if ctx.is_group:
                    await _remember_title(message.chat)
                await _require_admin(client, ctx, parsed)
                screen = await render(text, ctx)
            except AccessDenied as denied:
                await callback.answer(denied.message, show_alert=True)
                return
            except Exception:
                logger.exception("Button %r failed in chat %s", text[:40], ctx.chat_id)
                await callback.answer(ERROR_TEXT, show_alert=True)
                return
            if screen is None:
                await callback.answer("Nothing to show.")
                return
            # Personal numbers are sent only in private chat. Never post them as a
            # separate group message (that would still expose the stats to everyone).
            if needs_own_message(parsed, ctx):
                try:
                    private_ctx = Ctx(
                        user_id=int(sender.id), chat_id=int(sender.id), is_group=False,
                        first_name=sender.first_name or "", username=sender.username or "",
                        is_owner=int(sender.id) in SUDO_USERS,
                    )
                    private_text = f"/me {parsed.period} global u{int(sender.id)}"
                    private_screen = await render(private_text, private_ctx)
                    await client.send_message(
                        sender.id, private_screen.text if private_screen else screen.text,
                        parse_mode=html_parse_mode(), disable_web_page_preview=True,
                        reply_markup=private_screen.markup if private_screen else screen.markup,
                    )
                    await callback.answer("Your stats were sent privately.")
                except Exception:
                    from . import keyboards
                    username = getattr(getattr(client, "me", None), "username", None)
                    if username:
                        await callback.answer(
                            "Start the bot privately first, then tap My Stats again.",
                            show_alert=True, url=f"https://t.me/{username}?start=me",
                        )
                    else:
                        await callback.answer("Start the bot in private chat, then send /me.", show_alert=True)
                return
            await _cancel_rank_refresh(message.chat.id, message.id)
            try:
                await message.edit_text(
                    screen.text, parse_mode=html_parse_mode(), disable_web_page_preview=True,
                    reply_markup=screen.markup,
                )
            except Exception as exc:
                if _is_not_modified(exc):
                    await _start_rank_refresh(message, text, ctx)
                    await callback.answer("Already up to date.")
                    return
                logger.exception("Could not update menu in chat %s", ctx.chat_id)
                await callback.answer(ERROR_TEXT, show_alert=True)
                return
            await _start_rank_refresh(message, text, ctx)
            await callback.answer(screen.notice or "")

        @_bot_client.on_callback_query(filters.regex(r"^bc\|"))
        async def _on_broadcast_button(client, callback):
            parts = (callback.data or "").split("|")
            if len(parts) < 3:
                await callback.answer("This action is invalid.", show_alert=True)
                return
            action = parts[1]
            token = parts[2]
            mode = parts[3] if len(parts) > 3 else "cancel"
            await _send_broadcast(client, callback, token, mode if action == "send" else "cancel")

        @_bot_client.on_message(filters.new_chat_members)
        async def _on_added(client, message):
            me = client.me
            if not me or not any(member.id == me.id for member in message.new_chat_members):
                return
            if is_group_chat(message.chat):
                await _remember_title(message.chat)

            from . import screens
            from ..config import USER_SESSION_STRINGS
            from ..vc.telegram_monitor import ensure_assistants_in_group

            # Reply immediately; Assistant startup/invite APIs can be slow. Finish
            # setup in the background and edit this message when a result is known.
            welcome_message = await message.reply_text(
                screens.welcome() + "\nChecking Assistant setup…",
                parse_mode=html_parse_mode(), reply_markup=keyboards.open_menu(),
            )

            async def finish_assistant_setup():
                setup = {"ok": False, "reason": "no_connected_assistant", "ids": [], "joined": []}
                attempts = 7 if USER_SESSION_STRINGS else 1
                for attempt in range(attempts):
                    try:
                        setup = await ensure_assistants_in_group(client, int(message.chat.id)) if USER_SESSION_STRINGS else setup
                    except Exception as exc:
                        logger.warning("Assistant setup attempt failed for %s: %s", message.chat.id, exc)
                        setup = {**setup, "reason": "permission_or_api_error"}
                    if setup.get("ok"):
                        break
                    # The bot can be added before the optional MTProto client finishes
                    # connecting. Retry only the startup race; don't loop on permissions.
                    if setup.get("reason") == "no_connected_assistant" and attempt < attempts - 1:
                        await asyncio.sleep(5)
                        continue
                    break

                if setup.get("ok"):
                    ids = ", ".join(str(x) for x in setup.get("joined", []))
                    hint = f"\nAssistant ready: {ids}. VC logs enabled."
                else:
                    ids = setup.get("ids", [])
                    id_text = ", ".join(str(x) for x in ids) if ids else "not available yet — check /health"
                    reason = setup.get("reason")
                    if not USER_SESSION_STRINGS:
                        why = "Set SESSION_STRING or ASSISTANT_SESSION_STRINGS on Railway."
                    elif reason == "bot_not_admin":
                        why = "Make this bot a group admin, then add the Assistant manually."
                    elif reason == "no_invite_permission":
                        why = "Grant Invite Users permission or add the Assistant manually."
                    else:
                        why = "Add the Assistant account manually and give it access to this group."
                    hint = f"\nAssistant ID(s): {id_text}.\n{why} Detailed VC logs need an Assistant in this group."
                try:
                    await welcome_message.edit_text(
                        screens.welcome() + hint, parse_mode=html_parse_mode(),
                        disable_web_page_preview=True, reply_markup=keyboards.open_menu(),
                    )
                except Exception as exc:
                    logger.debug("Could not update Assistant setup message in %s: %s", message.chat.id, exc)

            task = asyncio.create_task(finish_assistant_setup(), name=f"assistant-setup-{message.chat.id}")
            _assistant_setup_tasks.add(task)
            task.add_done_callback(_assistant_setup_tasks.discard)

        logger.info("Telegram bot client ready.")
    except Exception as exc:
        logger.exception("Could not initialize Pyrogram bot client: %s", exc)
        _bot_client = None
    return _bot_client


async def stop_background_tasks() -> None:
    """Cancel menu refresh and Assistant-setup tasks during graceful shutdown."""
    tasks = set(_rank_refresh_tasks.values()) | set(_assistant_setup_tasks)
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _rank_refresh_tasks.clear()
    _assistant_setup_tasks.clear()


async def register_bot_commands(client) -> None:
    """Publish a short command menu for groups and a different one for private chats."""
    from pyrogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
    from .keyboards import set_bot_username

    set_bot_username(getattr(getattr(client, "me", None), "username", None))
    group_menu = [
        BotCommand("start", "Open menu"),
        BotCommand("help", "All commands"),
        BotCommand("vc", "Live voice chat"),
        BotCommand("stats", "Group stats"),
        BotCommand("top", "Top users"),
        BotCommand("groups", "Top groups"),
        BotCommand("me", "Your stats"),
        BotCommand("mystats", "Your stats"),
        BotCommand("history", "Recent VC history"),
        BotCommand("settings", "Group settings"),
        BotCommand("disappear", "Notice expiry"),
    ]
    private_menu = [
        BotCommand("start", "Open menu"),
        BotCommand("help", "All commands"),
        BotCommand("stats", "All-groups stats"),
        BotCommand("top", "Top users"),
        BotCommand("groups", "Top groups"),
        BotCommand("me", "Your stats"),
        BotCommand("mystats", "Your stats"),
    ]
    try:
        await client.set_bot_commands(group_menu, scope=BotCommandScopeAllGroupChats())
        await client.set_bot_commands(private_menu, scope=BotCommandScopeAllPrivateChats())
    except Exception:
        logger.exception("Scoped command menus are not supported; using one combined menu.")
        merged = {c.command: c for c in group_menu + private_menu}
        await client.set_bot_commands(list(merged.values()))
