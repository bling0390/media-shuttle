from __future__ import annotations

import atexit
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlparse

from ...logging import setup_logging
from ...models import DownloadResult, UploadResult
from .common import build_remote_name


_CHAT_ID_RE = re.compile(r"^-?\d+$")
_CHAT_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{4,}$")
_CLIENT_LOCK = Lock()
_TELEGRAM_CLIENT = None
_CLIENT_INVALIDATION_ERROR_NAMES = {
    "AuthKeyDuplicated",
    "ConnectionError",
    "ConnectionLost",
    "ConnectionResetError",
    "BrokenPipeError",
    "ConnectionAbortedError",
    "ConnectionRefusedError",
    "NetworkError",
    "TimeoutError",
    "TransportError",
}
logger = setup_logging()


@dataclass(frozen=True)
class TelegramChatTarget:
    chat_ref: str


def parse_telegram_destination(destination: str) -> TelegramChatTarget:
    raw = (destination or "").strip()
    if not raw:
        raise ValueError("telegram destination is required")

    parsed = urlparse(raw)
    if parsed.scheme != "tg" or parsed.netloc != "chat":
        raise ValueError("telegram destination must use tg://chat/<chat_ref>")

    chat_ref = unquote(parsed.path.lstrip("/")).strip()
    if not chat_ref:
        raise ValueError("telegram destination chat_ref is required")
    if "/" in chat_ref:
        raise ValueError("telegram destination must target a single chat")
    if not (_CHAT_ID_RE.fullmatch(chat_ref) or _CHAT_USERNAME_RE.fullmatch(chat_ref)):
        raise ValueError("telegram destination chat_ref must be @username or numeric chat id")

    return TelegramChatTarget(chat_ref=chat_ref)


def _env(name: str, fallback: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    if fallback:
        return os.getenv(fallback, "").strip()
    return ""


def _telegram_credentials() -> tuple[int, str, str]:
    api_id_raw = _env("MEDIA_SHUTTLE_TG_API_ID", "TELEGRAM_API_ID")
    api_hash = _env("MEDIA_SHUTTLE_TG_API_HASH", "TELEGRAM_API_HASH")
    bot_token = _env("MEDIA_SHUTTLE_TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")

    missing = [
        name
        for name, value in (
            ("MEDIA_SHUTTLE_TG_API_ID", api_id_raw),
            ("MEDIA_SHUTTLE_TG_API_HASH", api_hash),
            ("MEDIA_SHUTTLE_TG_BOT_TOKEN", bot_token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"telegram uploader requires env vars: {', '.join(missing)}")

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("MEDIA_SHUTTLE_TG_API_ID must be an integer") from exc

    return api_id, api_hash, bot_token


def _build_caption(download: DownloadResult) -> str | None:
    template = os.getenv("MEDIA_SHUTTLE_TG_UPLOAD_CAPTION_TEMPLATE", "{file_name}").strip()
    if not template:
        return None
    return template.format(
        file_name=download.file_name,
        site=download.site,
        source_url=download.source_url,
    )


def _build_telegram_client():
    api_id, api_hash, bot_token = _telegram_credentials()
    session_name = os.getenv("MEDIA_SHUTTLE_TG_SESSION_NAME", "media-shuttle-core-uploader").strip() or "media-shuttle-core-uploader"
    workdir = os.getenv("MEDIA_SHUTTLE_TG_WORKDIR", "/tmp/media-shuttle-tg").strip() or "/tmp/media-shuttle-tg"

    try:
        from pyrogram import Client
    except Exception as exc:
        raise RuntimeError("pyrogram is required for TELEGRAM live upload") from exc

    return Client(
        session_name,
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        workdir=workdir,
    )


def _close_telegram_client() -> None:
    global _TELEGRAM_CLIENT

    with _CLIENT_LOCK:
        client = _TELEGRAM_CLIENT
        _TELEGRAM_CLIENT = None

    if client is None:
        return

    try:
        client.stop()
    except Exception:
        pass
    logger.info("telegram client closed")


def _get_telegram_client():
    global _TELEGRAM_CLIENT

    with _CLIENT_LOCK:
        if _TELEGRAM_CLIENT is not None:
            return _TELEGRAM_CLIENT

        client = _build_telegram_client()
        client.start()
        _TELEGRAM_CLIENT = client
        logger.info("telegram client started")
        return client


def _should_invalidate_telegram_client(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True

    current: BaseException | None = exc
    while current is not None:
        if current.__class__.__name__ in _CLIENT_INVALIDATION_ERROR_NAMES:
            return True
        current = current.__cause__ or current.__context__
    return False


atexit.register(_close_telegram_client)


def upload_telegram_mock(download: DownloadResult, destination: str) -> UploadResult:
    remote_name = build_remote_name(download)
    return UploadResult(location=f"telegram://{destination.rstrip('/')}/{remote_name}")


def upload_telegram_live(download: DownloadResult, destination: str) -> UploadResult:
    path = Path(download.local_path)
    if not path.is_file():
        raise RuntimeError(f"downloaded file not found: {download.local_path}")

    target = parse_telegram_destination(destination)
    client = _get_telegram_client()
    caption = _build_caption(download)

    try:
        message = client.send_document(
            chat_id=target.chat_ref,
            document=str(path),
            caption=caption,
        )
    except Exception as exc:
        if _should_invalidate_telegram_client(exc):
            logger.warning(f"telegram upload invalidated client reason={exc}")
            _close_telegram_client()
        else:
            logger.warning(f"telegram upload failed without client invalidation reason={exc}")
        raise

    message_id = getattr(message, "id", None) or getattr(message, "message_id", None)
    if message_id is None:
        raise RuntimeError("telegram upload returned no message id")

    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None) if chat is not None else None
    location_chat = str(chat_id if chat_id is not None else target.chat_ref)
    return UploadResult(location=f"telegram://chat/{location_chat}/message/{message_id}")
