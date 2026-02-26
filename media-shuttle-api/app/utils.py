import hashlib
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    normalized = parts._replace(fragment="")
    return urlunsplit(normalized)


def make_idempotency_key(url: str, requester_id: str) -> str:
    raw = f"{normalize_url(url)}::{requester_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
