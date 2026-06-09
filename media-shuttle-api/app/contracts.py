from __future__ import annotations

import re
from urllib.parse import unquote, urlparse


_CHAT_ID_RE = re.compile(r"^-?\d+$")
_CHAT_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{4,}$")


# Default RCLONE destination when the caller omits it. Combined with the
# worker's ``MEDIA_SHUTTLE_USE_DATE_CATEGORY=1`` and the per-source
# ``remote_folder`` set by the parsers, this produces a final upload path
# of ``115:/<date>/<folder>/<file>``.
DEFAULT_RCLONE_DESTINATION = "115:/"


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
    required = ["url", "requester_id", "target"]
    for key in required:
        if key not in data or not str(data[key]).strip():
            raise ValueError(f"invalid field: {key}")
    if data["target"] not in {"RCLONE", "TELEGRAM"}:
        raise ValueError("invalid field: target")

    # destination is optional for RCLONE (defaults to ``115:/`` so the
    # final path is ``115:/<date>/<folder>/<file>``); required for
    # TELEGRAM because there's no sensible default chat.
    raw_destination = str(data.get("destination") or "").strip()
    if data["target"] == "RCLONE":
        if not raw_destination:
            data["destination"] = DEFAULT_RCLONE_DESTINATION
        else:
            data["destination"] = raw_destination
    else:
        if not raw_destination:
            raise ValueError("invalid field: destination")
        _validate_telegram_destination(raw_destination)
