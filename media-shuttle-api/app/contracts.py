from __future__ import annotations


def validate_create_request(data: dict) -> None:
    required = ["url", "requester_id", "target", "destination"]
    for key in required:
        if key not in data or not str(data[key]).strip():
            raise ValueError(f"invalid field: {key}")
    if data["target"] not in {"RCLONE", "TELEGRAM"}:
        raise ValueError("invalid field: target")
