from __future__ import annotations

import asyncio

from .api_client import ApiClient
from .handlers import TgHandlers
from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH


class BotRuntimeError(RuntimeError):
    pass


def run_bot() -> None:
    try:
        from pyrogram import Client, filters
    except Exception as exc:  # pragma: no cover
        raise BotRuntimeError("pyrogram is required to run media-shuttle-tg") from exc

    if not TELEGRAM_BOT_TOKEN:
        raise BotRuntimeError("TELEGRAM_BOT_TOKEN is required")

    api = ApiClient()
    handlers = TgHandlers(api)

    app = Client(
        "media-shuttle-tg",
        bot_token=TELEGRAM_BOT_TOKEN,
        api_id=TELEGRAM_API_ID,
        api_hash=TELEGRAM_API_HASH,
    )

    # Spin up the task-completion notifier once the bot is
    # actually running. We start it from inside a ``when_ready``
    # hook (registered below) so the pyrogram event loop is
    # already live when the notifier hands coroutines to it.
    notifier_holder: dict = {}

    @app.on_message(filters.command("leech") & filters.private)
    async def leech_command(_, message):
        args = message.text.split()
        if len(args) < 2:
            await message.reply(
                "Usage:\n"
                "  /leech <url> [destination]\n"
                "  /leech cleanup [dry]"
            )
            return

        sub = args[1].lower()

        # ``/leech cleanup`` (and ``/leech cleanup dry``) wipe
        # ``MEDIA_SHUTTLE_DOWNLOAD_DIR`` through the api. The
        # ``dry`` flag previews what would be removed without
        # actually deleting anything. Path safety is enforced
        # server-side in ``app.cleanup.sweep_download_dir`` so
        # every candidate is checked against the resolved
        # download root before removal.
        if sub == "cleanup":
            dry = len(args) >= 3 and args[2].lower() in {"dry", "dry-run", "preview"}
            result = handlers.on_cleanup_command(dry_run=dry)
            from .handlers import format_cleanup_reply
            await message.reply(format_cleanup_reply(result))
            return

        url = sub
        # destination is omitted: api defaults it to ``115:/`` so the
        # final upload path is ``115:/<date>/<folder>/<file>``. Callers
        # that need a custom subpath can add it as the second arg:
        # ``/leech <url> <destination>`` (still ``115:/<sub>`` form).
        destination = args[2] if len(args) >= 3 else None
        result = handlers.on_leech_command(
            requester_id=str(message.from_user.id),
            url=url,
            target="RCLONE",
            destination=destination,
        )
        await message.reply(f"queued: {result.get('task_id', '-')}")

    @app.on_message(filters.command("monitor") & filters.private)
    async def monitor_command(_, message):
        stat = handlers.on_monitor_command()
        await message.reply(str(stat))

    # Boot the task-completion notifier. ``app.run`` blocks; the
    # daemon thread exits when the process dies.
    try:
        from .notifier import TaskCompletedNotifier

        async def _start_notifier() -> None:
            loop = asyncio.get_running_loop()
            notifier = TaskCompletedNotifier(app, loop)
            notifier_holder["notifier"] = notifier
            notifier.start()

        app.loop.create_task(_start_notifier())
    except Exception as exc:  # pragma: no cover
        # Notifier is best-effort: bot still works for /leech.
        import logging
        logging.getLogger("media_shuttle.tg").warning(
            f"task notifier failed to start, /leech still works: {exc}"
        )

    app.run()


if __name__ == "__main__":  # pragma: no cover
    run_bot()
