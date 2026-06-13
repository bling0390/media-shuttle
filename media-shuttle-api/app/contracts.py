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


def validate_create_forum_request(data: dict) -> None:
    """Validate the body of ``POST /v1/tasks/parse_forum``.

    The forum dispatcher reuses the same target / destination
    semantics as :func:`validate_create_request`, so we
    delegate to that for the shared fields. ``max_pages`` is
    optional (0 = use env default) and capped at the env
    floor — callers can only ask for *fewer* pages than the
    global cap, not more.
    """
    # Re-use the regular parse request validation: same
    # url / requester_id / target / destination rules.
    subset = {
        "url": data.get("url", ""),
        "requester_id": data.get("requester_id", ""),
        "target": data.get("target", ""),
        "destination": data.get("destination", ""),
    }
    validate_create_request(subset)
    # Mirror the destination normalization done by
    # ``validate_create_request`` for RCLONE so the persisted
    # task record shows the resolved value, not the empty
    # string.
    data["destination"] = subset["destination"]

    raw_max = data.get("max_pages", 0)
    try:
        requested = int(raw_max)
    except (TypeError, ValueError):
        raise ValueError("invalid field: max_pages")
    if requested < 0:
        raise ValueError("invalid field: max_pages")
    # Apply the env floor (default 10). The operator cannot
    # raise the cap by passing a higher number — only lower
    # it.
    import os
    env_cap_raw = os.environ.get("FORUM_MAX_PAGES", "10").strip()
    try:
        env_cap = int(env_cap_raw)
    except (TypeError, ValueError):
        env_cap = 10
    if env_cap < 1:
        env_cap = 1
    if requested == 0 or requested > env_cap:
        data["max_pages"] = env_cap
    else:
        data["max_pages"] = requested


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
