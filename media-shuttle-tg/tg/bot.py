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
                "  /leech forum <url> [max_pages=N]\n"
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

        # ``/leech forum <url> [max_pages=N]`` walks a forum
        # thread, extracts the download links inside the
        # pages, dedupes bunkr mirrors + forum-internal
        # noise, and fans out the remaining links as
        # individual ``parse_link`` events onto the regular
        # ``task_created`` queue. The bot hands the forum
        # event to the api and replies immediately with the
        # forum task_id; the actual thread walk happens
        # asynchronously in a background core worker. Per-file
        # upload notifications arrive later via the regular
        # task.completed notifier once the fanned-out
        # ``parse_link`` events finish.
        #
        # ``max_pages=N`` is optional and can only *lower*
        # the server-side ``FORUM_MAX_PAGES`` cap, never
        # raise it. The cap is enforced by the api (see
        # ``validate_create_forum_request``).
        if sub == "forum":
            if len(args) < 3:
                await message.reply("Usage: /leech forum <url> [max_pages=N]")
                return
            url = args[2].strip()
            if not url:
                await message.reply("Usage: /leech forum <url> [max_pages=N]")
                return
            max_pages: int | None = None
            if len(args) >= 4:
                tail = args[3].strip()
                if tail.startswith("max_pages="):
                    tail = tail.split("=", 1)[1]
                try:
                    max_pages = int(tail)
                except ValueError:
                    await message.reply(
                        f"invalid max_pages={tail!r}; expected integer"
                    )
                    return
            result = handlers.on_leech_forum_command(
                requester_id=str(message.from_user.id),
                url=url,
                target="RCLONE",
                max_pages=max_pages,
            )
            await message.reply(
                f"forum task queued: {result.get('task_id', '-')} "
                f"(max_pages={max_pages or 'default'})"
            )
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

    @app.on_callback_query()
    async def retry_callback(_, callback_query):
        """Handle inline retry buttons on failure notifications.

        Callback data format: ``retry_<phase>:<task_id>``
        where ``<phase>`` is one of:

        * ``dl`` — re-queue the download phase.
        * ``ul`` — re-queue the upload phase.
        * ``fr`` — re-walk a forum thread.
        * ``all`` — generic retry (reserved for future
          phases that don't have a custom label).

        The phase suffix is accepted but currently
        ignored on the api side: every retry is a full
        re-run of the task. The reason is that the local
        download may have been cleaned up by the time the
        operator clicks (see
        ``core/queue/tasks.py::process_finalize_task_logic``),
        and a partial re-run would have to recreate the
        parser's source list from cache, which the
        pipeline does not expose yet.

        On click we:

        1. Acknowledge the tap with ``answerCallbackQuery``
           so the button stops spinning and the operator
           gets a short toast.
        2. Try to edit the original failure message in
           place (``editMessageText``) so the chat history
           is not flooded with "重试中" + the next
           per-file notification. If we don't have the
           (chat_id, message_id) index in redis (e.g. the
           bot was restarted) we fall back to a fresh
           reply; the button still works.
        3. Hand off to ``TgHandlers.on_retry_task_command``
           which translates api responses into a small
           structured dict and we use the ``code`` field
           to pick a follow-up toast.
        """
        data = (callback_query.data or "").strip()
        if not data.startswith("retry_"):
            # We only handle the retry prefix; if some
            # other callback gets routed here (e.g. a
            # future feature) we just ack and let the
            # default handler deal with it.
            await callback_query.answer()
            return
        token, sep, task_id = data[len("retry_"):].partition(":")
        if not sep or not task_id:
            await callback_query.answer(
                "按钮数据损坏",
                show_alert=True,
            )
            return
        requester_id = str(callback_query.from_user.id)
        result = handlers.on_retry_task_command(
            task_id=task_id,
            requester_id=requester_id,
        )
        # Look up the original message so we can edit it
        # in place. We tolerate the redis lookup failing:
        # the worst case is a new "重试中" message appears
        # in the chat, the retry itself still went through.
        original = None
        try:
            import redis as redis_lib
            import json as json_lib
            from .notifier import _retry_msg_key, _redis_url
            client = redis_lib.Redis.from_url(_redis_url())
            raw = client.get(_retry_msg_key(task_id))
            if raw:
                payload = json_lib.loads(raw)
                original = (
                    int(payload.get("chat_id") or 0),
                    int(payload.get("message_id") or 0),
                )
        except Exception:
            original = None
        if original and original[0] and original[1]:
            # The original failure notification is still
            # in the chat; replace its body with the
            # follow-up. The reply_markup is dropped so
            # the operator can't double-click.
            follow_up = result.get("message") or ""
            if result.get("ok"):
                suffix = "（新状态将通过下一条消息推送）"
                follow_up = f"{follow_up}{suffix}"
            try:
                await app.edit_message_text(
                    chat_id=original[0],
                    message_id=original[1],
                    text=follow_up or "重试中…",
                )
            except Exception:
                # ``editMessageText`` raises on a deleted
                # message, a permissions change, or a
                # message that's too old. Fall back to a
                # fresh reply so the operator still sees
                # the result.
                await callback_query.message.reply(follow_up or "重试中…")
        else:
            # No index in redis; the original message
            # location is unknown (bot restarted between
            # notification and click, or we just never
            # stored it). Send a fresh reply so the
            # operator isn't left hanging.
            follow_up = result.get("message") or "重试中…"
            try:
                await callback_query.message.reply(follow_up)
            except Exception:
                pass
        # ``answerCallbackQuery`` stops the button from
        # spinning and shows the toast. ``show_alert``
        # is reserved for failure toasts so the operator
        # doesn't miss a 404 / 409.
        if result.get("ok"):
            await callback_query.answer(result.get("message") or "已重试")
        else:
            await callback_query.answer(
                result.get("message") or "重试失败",
                show_alert=True,
            )

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
