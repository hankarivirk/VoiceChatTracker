"""Telegram wiring only: receive commands/buttons, call the router, send the result."""
import asyncio
from ..config import BOT_TOKEN, API_ID, API_HASH, SUDO_USERS
from ..utils.logging import logger
from ..utils.telegram_compat import html_parse_mode, is_group_chat
from .context import AccessDenied, Ctx

_bot_client = None
_known_titles: dict[int, str] = {}
_rank_refresh_tasks: dict[tuple[int, int], asyncio.Task] = {}

ERROR_TEXT = "Something went wrong. Please try again in a moment."


def _context(chat, sender) -> Ctx:
    uid = int(getattr(sender, "id", 0) or 0)
    fname = getattr(sender, "first_name", getattr(sender, "title", "")) or ""
    uname = getattr(sender, "username", "") or ""
    return Ctx(
        user_id=uid,
        chat_id=int(chat.id),
        is_group=is_group_chat(chat),
        first_name=str(fname),
        username=str(uname),
        is_owner=uid in SUDO_USERS,
    )


def _is_not_modified(exc: Exception) -> bool:
    return type(exc).__name__ == "MessageNotModified" or "MESSAGE_NOT_MODIFIED" in str(exc).upper()


async def _remember_title(chat) -> None:
    """Store the group name once (not on every command) so rankings show real names."""
    title = getattr(chat, "title", None)
    if not title or _known_titles.get(int(chat.id)) == title:
        return
    from ..database.groups import update_group_settings
    await update_group_settings(int(chat.id), {"title": title})
    _known_titles[int(chat.id)] = title


async def _require_admin(client, ctx: Ctx, parsed) -> None:
    if parsed.mutates and not ctx.is_owner:
        if ctx.is_group and ctx.user_id == ctx.chat_id:
            return  # Anonymous admin posting on behalf of the group
        from ..utils.permissions import is_admin
        if not await is_admin(client, ctx.chat_id, ctx.user_id):
            raise AccessDenied("Only group admins can change settings.")


async def _cancel_rank_refresh(chat_id: int, message_id: int) -> None:
    task = _rank_refresh_tasks.pop((int(chat_id), int(message_id)), None)
    if task and not task.done():
        task.cancel()


async def cancel_all_rank_refreshes() -> None:
    tasks = list(_rank_refresh_tasks.values())
    _rank_refresh_tasks.clear()
    for task in tasks:
        if task and not task.done():
            task.cancel()


async def _start_rank_refresh(message, text: str, ctx: Ctx) -> None:
    """Refresh an open ranking message every 15s; cancel when navigated away."""
    parsed = __import__("VCBlogger.bot.router", fromlist=["parse"]).parse(text)
    key = (int(message.chat.id), int(message.id))
    await _cancel_rank_refresh(*key)
    if not parsed or parsed.name not in {"top", "groups", "history"}:
        return

    async def refresh_loop():
        from .router import render
        try:
            iterations = 0
            while iterations < 480:  # 2 hours max auto-refresh for an open screen
                await asyncio.sleep(15)
                iterations += 1
                screen = await render(text, ctx)
                if screen is None:
                    return
                try:
                    await message.edit_text(
                        screen.text, parse_mode=html_parse_mode(), disable_web_page_preview=True,
                        reply_markup=screen.markup,
                    )
                except Exception as exc:
                    if _is_not_modified(exc):
                        continue
                    raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Ranking auto-refresh stopped for %s: %s", key, exc)
        finally:
            if _rank_refresh_tasks.get(key) is asyncio.current_task():
                _rank_refresh_tasks.pop(key, None)

    _rank_refresh_tasks[key] = asyncio.create_task(refresh_loop(), name=f"rank-refresh-{key[0]}-{key[1]}")


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
                sender_chat = getattr(message, "sender_chat", None)
                if sender_chat and is_group_chat(message.chat):
                    sender = sender_chat
                else:
                    return
            ctx = _context(message.chat, sender)
            text = message.text or message.caption or ""
            try:
                if ctx.is_group:
                    await _remember_title(message.chat)
                parsed = parse(text)
                if parsed is None:
                    return
                await _require_admin(client, ctx, parsed)
                screen = await render(text, ctx)
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
            await _cancel_rank_refresh(message.chat.id, message.id)
            parsed = parse(text)
            if parsed is None:
                await callback.answer("This button is outdated. Send /start.", show_alert=True)
                return
            # Personal cards (your stats) only respond to the person they belong to.
            if is_foreign_card(parsed, sender.id):
                await callback.answer("This card is someone else's. Send /me for yours.", show_alert=True)
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
            # 'My stats' pressed on a shared group message gets its own message, so one
            # person's numbers never replace what everyone else is looking at.
            if needs_own_message(parsed, ctx):
                await message.reply_text(
                    screen.text, parse_mode=html_parse_mode(), disable_web_page_preview=True,
                    reply_markup=screen.markup,
                )
                await callback.answer()
                return
            try:
                await message.edit_text(
                    screen.text, parse_mode=html_parse_mode(), disable_web_page_preview=True,
                    reply_markup=screen.markup,
                )
            except Exception as exc:
                if _is_not_modified(exc):
                    await callback.answer("Already up to date.")
                    return
                logger.exception("Could not update menu in chat %s", ctx.chat_id)
                await callback.answer(ERROR_TEXT, show_alert=True)
                return
            await _start_rank_refresh(message, text, ctx)
            await callback.answer(screen.notice or "")

        @_bot_client.on_message(filters.new_chat_members)
        async def _on_added(client, message):
            me = getattr(client, "me", None)
            if me is None:
                try:
                    me = await client.get_me()
                except Exception:
                    pass
            if me and any(member.id == me.id for member in message.new_chat_members):
                if is_group_chat(message.chat):
                    await _remember_title(message.chat)
                from . import screens
                from ..config import USER_SESSION_STRINGS
                from ..vc.telegram_monitor import ensure_assistants_in_group
                setup = await ensure_assistants_in_group(client, int(message.chat.id)) if USER_SESSION_STRINGS else {"ok": False, "reason": "no_connected_assistant", "ids": [], "joined": []}
                if setup.get("ok"):
                    assistant_hint = "\nAssistant account added/verified: " + ", ".join(str(x) for x in setup.get("joined", [])) + ". VC logs are ready."
                else:
                    ids = setup.get("ids", [])
                    id_text = ", ".join(str(x) for x in ids) if ids else "not available yet — check /health after Assistant startup"
                    if setup.get("reason") == "bot_not_admin":
                        why = "Make this bot an admin, then add the Assistant account manually."
                    elif setup.get("reason") == "no_invite_permission":
                        why = "The bot is admin but lacks Invite Users permission. Add the Assistant account manually."
                    elif not USER_SESSION_STRINGS:
                        why = "Configure SESSION_STRING or ASSISTANT_SESSION_STRINGS on Railway first."
                    else:
                        why = "Auto-join was unavailable. Add the Assistant account manually."
                    assistant_hint = f"\nAssistant ID(s): {id_text}.\n{why} Detailed VC logs require the Assistant to be in this group."
                await message.reply_text(
                    screens.welcome() + assistant_hint, parse_mode=html_parse_mode(),
                    reply_markup=keyboards.open_menu(),
                )

        logger.info("Telegram bot client ready.")
    except Exception as exc:
        logger.exception("Could not initialize Pyrogram bot client: %s", exc)
        _bot_client = None
    return _bot_client


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
