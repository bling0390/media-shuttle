"""Runtime configuration for the forum dispatcher.

The forum dispatcher has a small set of knobs and almost all of them
are safer as env vars than as dataclass config: a worker can be
re-deployed with different limits without code changes, and we avoid
the chicken-and-egg of "the config object isn't built when the env
is read at import time". Everything reads from ``os.environ`` lazily
on first access via module-level helpers.
"""

from __future__ import annotations

import os


def max_pages(default: int = 10) -> int:
    """Hard cap on how many pages a single forum task may traverse.

    XenForo threads with hundreds of pages are common; without a
    cap a single ``/leech forum`` would queue hundreds of HTTP
    fetches and potentially a thousand fan-out tasks. The cap is
    measured in *pages*, not posts: a typical page is ~20 posts
    and ~5-10 parseable links.

    The cap is overridable per task at submission time (the
    ``max_pages`` arg on the api request), but only ever *lowered*
    by env (the runtime floor).
    """
    raw = os.environ.get("FORUM_MAX_PAGES", str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, value)


def fanout_cap(default: int = 200) -> int:
    """Hard cap on how many parse_link tasks a single forum task may spawn.

    This is a *safety* cap, not a tuning knob: the operator's
    intent is "give me everything in this thread", but the queue
    is shared with every other user / task. 200 is a sane
    upper bound that keeps the celery broker and downstream
    workers from being DOS'd by a single fat thread.

    This cap is NOT exposed to the user via ``/leech forum``.
    If the cap is hit, the forum task finalizes with a message
    explaining how many were truncated and the user can re-run
    with a deeper ``max_pages`` or just trust the truncation.
    """
    raw = os.environ.get("FORUM_FANOUT_CAP", str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, value)


# Sleep schedule. All times are in seconds and read fresh on every
# call so an operator can hot-tune in dev (override the env, run
# the next task) without bouncing the worker.

_BASE_SLEEP_MIN_S = 2.0
_BASE_SLEEP_MAX_S = 4.0
_BURST_SLEEP_MIN_S = 8.0
_BURST_SLEEP_MAX_S = 15.0
_CIRCUIT_SLEEP_MIN_S = 30.0
_CIRCUIT_SLEEP_MAX_S = 60.0
_BURST_INTERVAL = 5  # every N pages
_CIRCUIT_INTERVAL = 50  # every N pages, cumulative


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def base_sleep_range() -> tuple[float, float]:
    return (
        _float_env("FORUM_BASE_SLEEP_MIN_S", _BASE_SLEEP_MIN_S),
        _float_env("FORUM_BASE_SLEEP_MAX_S", _BASE_SLEEP_MAX_S),
    )


def burst_sleep_range() -> tuple[float, float]:
    return (
        _float_env("FORUM_BURST_SLEEP_MIN_S", _BURST_SLEEP_MIN_S),
        _float_env("FORUM_BURST_SLEEP_MAX_S", _BURST_SLEEP_MAX_S),
    )


def circuit_sleep_range() -> tuple[float, float]:
    return (
        _float_env("FORUM_CIRCUIT_SLEEP_MIN_S", _CIRCUIT_SLEEP_MIN_S),
        _float_env("FORUM_CIRCUIT_SLEEP_MAX_S", _CIRCUIT_SLEEP_MAX_S),
    )


def burst_interval() -> int:
    raw = os.environ.get("FORUM_BURST_INTERVAL", str(_BURST_INTERVAL)).strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _BURST_INTERVAL


def circuit_interval() -> int:
    raw = os.environ.get("FORUM_CIRCUIT_INTERVAL", str(_CIRCUIT_INTERVAL)).strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _CIRCUIT_INTERVAL
