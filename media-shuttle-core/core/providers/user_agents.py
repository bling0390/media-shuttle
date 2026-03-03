from __future__ import annotations

import os
import random

_DEFAULT_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/133.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
)


def _env_user_agents() -> tuple[str, ...]:
    raw = os.getenv("MEDIA_SHUTTLE_USER_AGENTS", "").strip()
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split("||") if item.strip())


def get_random_user_agent() -> str:
    # Explicit single-value override has the highest priority.
    explicit = os.getenv("MEDIA_SHUTTLE_USER_AGENT", "").strip()
    if explicit:
        return explicit

    pool = _env_user_agents() or _DEFAULT_USER_AGENTS
    return random.choice(pool)


def with_random_user_agent(headers: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(headers or {})
    if not merged.get("User-Agent"):
        merged["User-Agent"] = get_random_user_agent()
    return merged

