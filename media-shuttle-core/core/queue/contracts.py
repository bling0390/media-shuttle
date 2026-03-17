from __future__ import annotations

from datetime import datetime
from urllib.parse import unquote, urlparse
import re


_CHAT_ID_RE = re.compile(r"^-?\d+$")
_CHAT_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{4,}$")


class ContractError(ValueError):
    pass


def _required(payload: dict, fields: list[str]) -> None:
    for field in fields:
        if field not in payload:
            raise ContractError(f"missing field: {field}")


def _validate_telegram_destination(destination: str) -> None:
    parsed = urlparse(destination)
    if parsed.scheme != "tg" or parsed.netloc != "chat":
        raise ContractError("invalid telegram destination")

    chat_ref = unquote(parsed.path.lstrip("/")).strip()
    if not chat_ref or "/" in chat_ref:
        raise ContractError("invalid telegram destination")
    if not (_CHAT_ID_RE.fullmatch(chat_ref) or _CHAT_USERNAME_RE.fullmatch(chat_ref)):
        raise ContractError("invalid telegram destination")


def validate_task_created_event(event: dict) -> None:
    _required(event, ["spec_version", "task_id", "task_type", "idempotency_key", "created_at", "payload"])
    if event["spec_version"] != "task.created.v1":
        raise ContractError("invalid spec_version")
    if event["task_type"] != "parse_link":
        raise ContractError("invalid task_type")
    datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
    payload = event["payload"]
    _required(payload, ["url", "requester_id", "target", "destination"])
    if payload["target"] == "TELEGRAM":
        _validate_telegram_destination(str(payload["destination"]))


def validate_task_status_event(event: dict) -> None:
    _required(event, ["spec_version", "task_id", "status", "updated_at"])
    if event["spec_version"] != "task.status.v1":
        raise ContractError("invalid spec_version")
    datetime.fromisoformat(event["updated_at"].replace("Z", "+00:00"))
