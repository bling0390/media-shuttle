from __future__ import annotations


def upload_tool_buttons(prefix: str = "leech_sync_") -> list[dict]:
    return [
        {"text": "RCLONE", "callback_data": f"{prefix}RCLONE"},
        {"text": "TELEGRAM", "callback_data": f"{prefix}TELEGRAM"},
    ]


def telegram_destination_buttons(prefix: str = "leech_dest_") -> list[dict]:
    return [
        {"text": "Channel", "callback_data": f"{prefix}channel"},
        {"text": "Private", "callback_data": f"{prefix}private"},
    ]
