"""VCBlogger entry point: keep the Bot API responsive while optional startup work runs."""
import asyncio
import signal

from VCBlogger.config import (
    APP_VERSION, AUTO_RECOVERY, SNAPSHOT_INTERVAL, VC_RECONCILE_INTERVAL_SECONDS,
    validate_required_config,
)
from VCBlogger.utils.logging import logger
from VCBlogger.database.mongo import get_db, close as close_mongo
from VCBlogger.database.maintenance import run_retention_cleanup, retention_loop, storage_alert_loop
from VCBlogger.database.backup import backup_loop
from VCBlogger.database.users import reconcile_user_aggregates
from VCBlogger.database.redis_cache import close as close_redis, is_configured as redis_configured
from VCBlogger.database.postgres_archive import close as close_postgres, is_configured as postgres_configured
from VCBlogger.bot.client import get_bot, register_bot_commands, cancel_all_rank_refreshes
from VCBlogger.vc.recovery import recovery_engine
from VCBlogger.vc.telegram_monitor import (
    get_user_monitors, discover_active_group_calls, reconcile_active_group_calls, set_monitor_connected,
)
from VCBlogger.vc.monitor import vc_monitor
from VCBlogger.vc.sessions import session_manager


async def _run_startup_maintenance():
    """Run potentially slow DB repair after Telegram commands are already online."""
    try:
        await run_retention_cleanup()
        logger.info("Startup maintenance: reconciling durable VC-time ledger.")
        repaired = await reconcile_user_aggregates()
        logger.info("Startup maintenance finished; reconciled %s user aggregate(s).", repaired)
        if AUTO_RECOVERY:
            recovered = await recovery_engine.recover_dangling_sessions()
            logger.info("Startup recovery finished; recovered %s interrupted session(s).", recovered)
        else:
            logger.warning("AUTO_RECOVERY is disabled. Dangling sessions remain for manual review.")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Startup database maintenance failed; bot remains online and maintenance can be retried on restart.")


async def _start_assistants(active_user_monitors: list):
    """Start optional MTProto user sessions without blocking Bot API startup."""
    try:
        user_monitors = get_user_monitors()
    except Exception:
        logger.exception("Could not initialize optional Assistant sessions; Bot API remains available.")
        set_monitor_connected(False)
        return

    if not user_monitors:
        logger.warning(
            "No MTProto Assistant session configured. Bot commands are online, but detailed VC participant tracking is unavailable. "
            "Configure SESSION_STRING or ASSISTANT_SESSION_STRINGS to enable it."
        )
        set_monitor_connected(False)
        return

    startup_errors = []
    for index, user_monitor in enumerate(user_monitors, start=1):
        try:
            # Bound connection time so a stale session/network issue cannot block other services.
            await asyncio.wait_for(user_monitor.start(), timeout=30)
            monitor_identity = getattr(user_monitor, "me", None)
            if bool(getattr(monitor_identity, "is_bot", False)):
                raise RuntimeError("This session belongs to a bot account; use a logged-in Telegram user account.")
            account_id = getattr(monitor_identity, "id", None)
            existing_ids = {
                getattr(getattr(existing_monitor, "me", None), "id", None)
                for existing_monitor in active_user_monitors
            }
            if account_id is not None and account_id in existing_ids:
                raise RuntimeError(f"Telegram account ID {account_id} is configured more than once; use distinct user accounts.")
            set_monitor_connected(True, user_monitor)
            active_user_monitors.append(user_monitor)
            logger.info("MTProto Assistant %s connected as account ID %s.", index, account_id or "unknown")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            startup_errors.append(f"Assistant {index}: {type(exc).__name__}: {exc}")
            logger.exception("MTProto Assistant %s could not start; bot commands remain available.", index)
            set_monitor_connected(False, user_monitor)
            try:
                await asyncio.wait_for(user_monitor.stop(), timeout=10)
            except Exception:
                pass

    if not active_user_monitors:
        set_monitor_connected(False)
        logger.error("No Assistant connected. Bot API remains online; detailed participant detection needs a valid user session.")
        return

    if startup_errors:
        logger.warning("Some optional Assistant sessions were unavailable; %s account(s) connected.", len(active_user_monitors))

    # Startup discovery can be slow across many dialogs, so it runs only after the bot is online.
    for index, user_monitor in enumerate(list(active_user_monitors), start=1):
        try:
            await asyncio.wait_for(discover_active_group_calls(user_monitor), timeout=90)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Startup VC discovery failed for Assistant %s; live update monitoring remains enabled.", index)


async def main():
    logger.info("Starting VCBlogger v%s (Bot API starts first; MTProto Assistants are optional).", APP_VERSION)
    validate_required_config()
    await get_db()
    logger.info("Database connected; starting Telegram bot before slow history reconciliation.")

    bot = get_bot()
    if bot is None:
        raise RuntimeError("Could not initialize Telegram bot client; check BOT_TOKEN, API_ID and API_HASH.")
    await bot.start()
    vc_monitor.bot = bot
    await register_bot_commands(bot)
    logger.info("Telegram bot started and command menu registered; it is ready to receive commands.")
    if redis_configured():
        logger.info("Redis cache is configured; see Owner panel > Health.")
    if postgres_configured():
        logger.info("Neon/PostgreSQL archive is configured; see Owner panel > Health.")

    active_user_monitors = []
    maintenance_task = asyncio.create_task(_run_startup_maintenance(), name="vc-startup-db-maintenance")
    assistant_startup_task = asyncio.create_task(_start_assistants(active_user_monitors), name="vc-assistant-startup")

    async def snapshot_loop():
        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            try:
                await session_manager.snapshot_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("VC snapshot loop failed; it will retry next interval.")

    async def vc_reconcile_loop():
        while True:
            await asyncio.sleep(VC_RECONCILE_INTERVAL_SECONDS)
            if not active_user_monitors:
                continue
            try:
                counts = await asyncio.gather(
                    *(reconcile_active_group_calls(monitor) for monitor in list(active_user_monitors)),
                    return_exceptions=True,
                )
                count = sum(item for item in counts if isinstance(item, int))
                logger.debug("Periodic VC roster reconciliation completed for %s active call(s).", count)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Periodic VC roster reconciliation loop failed; it will retry next interval.")

    snapshot_task = asyncio.create_task(snapshot_loop(), name="vc-session-snapshot-loop")
    vc_reconcile_task = asyncio.create_task(vc_reconcile_loop(), name="vc-roster-reconcile-loop")
    cleanup_task = asyncio.create_task(retention_loop(), name="vc-storage-retention-loop")
    storage_alert_task = asyncio.create_task(storage_alert_loop(bot), name="vc-storage-alert-loop")
    backup_task = asyncio.create_task(backup_loop(bot), name="vc-backup-loop")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop_event.wait()
    finally:
        logger.info("Stopping VCBlogger services...")
        tasks = [snapshot_task, vc_reconcile_task, cleanup_task, storage_alert_task, backup_task,
                 maintenance_task, assistant_startup_task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error while stopping a VCBlogger background task.")
        try:
            await cancel_all_rank_refreshes()
        except Exception:
            pass
        try:
            await session_manager.snapshot_all()
        except Exception as exc:
            logger.warning("Final session snapshot failed: %s", exc)
        try:
            await bot.stop()
        except Exception as exc:
            logger.warning("Telegram bot stop warning: %s", exc)
        for user_monitor in active_user_monitors:
            try:
                await user_monitor.stop()
            except Exception as exc:
                logger.warning("MTProto Assistant stop warning: %s", exc)
        set_monitor_connected(False)
        await close_redis()
        await close_postgres()
        await close_mongo()
        logger.info("VCBlogger stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
