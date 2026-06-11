"""Manual local-download cleanup helpers (api-side).

The ``core`` package has its own ``cleanup_local_download`` /
``sweep_download_dir`` pair that runs inside the worker
container, but the api image is a different deployment unit
without the core package on its ``sys.path``. This module
provides a small, dependency-free equivalent used by the
``POST /v1/admin/cleanup-downloads`` endpoint (and, by
extension, the ``/leech cleanup`` Telegram command).

The path-safety contract is identical to the core version:
every candidate is checked against the resolved download root
before any removal, so a misconfigured ``MEDIA_SHUTTLE_DOWNLOAD_DIR``
can never turn this command into a ``rm -rf`` over the host
filesystem.
"""

from __future__ import annotations

import os
from pathlib import Path


def _dir_size_bytes(path: Path) -> int:
    """Best-effort total size of ``path`` in bytes.

    Symlink targets are counted (the worker materializes a
    single ``tmp.part`` per task so symlinks are not used in
    practice, but the helper stays safe under extensions).
    Errors reading a child are swallowed so a single
    permission failure does not nuke the whole report.
    """
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _is_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _remove_inside(child: Path, root: Path) -> bool:
    """Remove ``child`` (file, symlink, or directory tree) and
    prune empty ancestors up to (but excluding) ``root``.

    Returns True if anything was actually removed, False if
    the path lived outside the download root or did not
    exist.
    """
    if not _is_inside(root, child):
        return False

    try:
        if child.is_file() or child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            for sub in child.rglob("*"):
                if sub.is_file() or sub.is_symlink():
                    sub.unlink(missing_ok=True)
            for sub in sorted(child.rglob("*"), reverse=True):
                if sub.is_dir():
                    try:
                        sub.rmdir()
                    except OSError:
                        pass
            try:
                child.rmdir()
            except OSError:
                pass
        else:
            return False
    except Exception:
        return False

    # Prune empty parent folders under download root, but keep root itself.
    parent = child.parent
    root_resolved = root.resolve()
    while parent != root_resolved:
        try:
            parent.relative_to(root_resolved)
        except Exception:
            break
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

    return True


def sweep_download_dir(dry_run: bool = False) -> dict:
    """Walk ``MEDIA_SHUTTLE_DOWNLOAD_DIR`` and remove every
    artifact left by completed/failed/cancelled tasks.

    The function only touches direct children of the
    configured download root. Each child is a single
    ``<sha1-seed>/tmp.part`` tree produced by the worker's
    ``materialize_path`` helper (or, for custom extensions,
    an entire plugin output directory). Every candidate is
    path-checked against the resolved download root before
    removal.

    Returns a summary dict that the api endpoint and the
    Telegram handler both surface verbatim to the operator:

    .. code-block:: python

        {
            "root": "/tmp/media-shuttle",
            "dry_run": False,
            "scanned": 12,        # direct children seen
            "removed": 10,        # successful cleanups
            "skipped": 2,         # safety-rejected or absent
            "freed_bytes": 268435456,
            "items": [
                {"path": "...", "size_bytes": 123,
                 "kind": "dir|file|symlink",
                 "removed": True, "reason": "..." (optional)},
                ...
            ],
        }
    """
    download_root = Path(
        os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR", "/tmp/media-shuttle")
    )

    summary: dict = {
        "root": str(download_root),
        "dry_run": bool(dry_run),
        "scanned": 0,
        "removed": 0,
        "skipped": 0,
        "freed_bytes": 0,
        "items": [],
    }

    if not download_root.is_dir():
        return summary

    for child in sorted(download_root.iterdir()):
        summary["scanned"] += 1

        if not _is_inside(download_root, child):
            summary["skipped"] += 1
            summary["items"].append(
                {
                    "path": str(child),
                    "size_bytes": 0,
                    "kind": "unknown",
                    "removed": False,
                    "reason": "outside_download_root",
                }
            )
            continue

        if child.is_dir():
            size = _dir_size_bytes(child)
        elif child.is_file() or child.is_symlink():
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
        else:
            size = 0

        kind = (
            "dir"
            if child.is_dir()
            else ("symlink" if child.is_symlink() else "file")
        )

        if dry_run:
            summary["items"].append(
                {
                    "path": str(child),
                    "size_bytes": size,
                    "kind": kind,
                    "removed": False,
                }
            )
            continue

        if _remove_inside(child, download_root):
            summary["removed"] += 1
            summary["freed_bytes"] += size
            summary["items"].append(
                {
                    "path": str(child),
                    "size_bytes": size,
                    "kind": kind,
                    "removed": True,
                }
            )
        else:
            summary["skipped"] += 1
            summary["items"].append(
                {
                    "path": str(child),
                    "size_bytes": size,
                    "kind": kind,
                    "removed": False,
                    "reason": "remove_returned_false",
                }
            )

    return summary
