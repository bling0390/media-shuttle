from __future__ import annotations

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

    @app.on_message(filters.command("leech") & filters.private)
    async def leech_command(_, message):
        args = message.text.split()
        if len(args) < 2:
            await message.reply("Usage: /leech <url>")
            return
        url = args[1]
        result = handlers.on_leech_command(
            requester_id=str(message.from_user.id),
            url=url,
            target="RCLONE",
            destination="/",
        )
        await message.reply(f"queued: {result.get('task_id', '-')}")

    @app.on_message(filters.command("monitor") & filters.private)
    async def monitor_command(_, message):
        stat = handlers.on_monitor_command()
        await message.reply(str(stat))

    app.run()


if __name__ == "__main__":  # pragma: no cover
    run_bot()
