from __future__ import annotations

import re
from urllib.parse import unquote, urlparse


_CHAT_ID_RE = re.compile(r"^-?\d+$")
_CHAT_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{4,}$")


def _validate_telegram_destination(destination: str) -> None:
    parsed = urlparse(destination)
    if parsed.scheme != "tg" or parsed.netloc != "chat":
        raise ValueError("invalid field: destination")

    chat_ref = unquote(parsed.path.lstrip("/")).strip()
    if not chat_ref or "/" in chat_ref:
        raise ValueError("invalid field: destination")
    if not (_CHAT_ID_RE.fullmatch(chat_ref) or _CHAT_USERNAME_RE.fullmatch(chat_ref)):
        raise ValueError("invalid field: destination")


def validate_create_request(data: dict) -> None:
    required = ["url", "requester_id", "target", "destination"]
    for key in required:
        if key not in data or not str(data[key]).strip():
            raise ValueError(f"invalid field: {key}")
    if data["target"] not in {"RCLONE", "TELEGRAM"}:
        raise ValueError("invalid field: target")
    if data["target"] == "TELEGRAM":
        _validate_telegram_destination(str(data["destination"]))
