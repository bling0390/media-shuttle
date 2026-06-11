import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    normalized = parts._replace(fragment="")
    return urlunsplit(normalized)


def make_idempotency_key(url: str, requester_id: str) -> str:
    raw = f"{normalize_url(url)}::{requester_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def bool_env(name: str, default: str = "0") -> bool:
    raw = os.getenv(name, default).strip().lower()
    return raw not in {"", "0", "false", "off", "no"}


def cleanup_local_download(local_path: str) -> bool:
    if not bool_env("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS", "1"):
        return False
    if not local_path:
        return False

    download_root = Path(os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR", "/tmp/media-shuttle"))
    try:
        root_resolved = download_root.resolve()
        path = Path(local_path).resolve()
        path.relative_to(root_resolved)
    except Exception:
        # Only cleanup artifacts inside configured download root.
        return False

    removed = False
    try:
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
            removed = True
        elif path.is_dir():
            # Current builtins store files, but keep directory support for extensions.
            for child in path.rglob("*"):
                if child.is_file() or child.is_symlink():
                    child.unlink(missing_ok=True)
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_dir():
                    try:
                        child.rmdir()
                    except OSError:
                        pass
            try:
                path.rmdir()
            except OSError:
                pass
            removed = True
    except Exception:
        return False

    # Prune empty parent folders under download root, but keep root itself.
    parent = path.parent
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

    return removed


def _dir_size_bytes(path: Path) -> int:
    """Best-effort total size of ``path`` in bytes.

    Used by the manual cleanup command to report how much disk
    space will be freed. Symlinks are followed only when the
    target is a regular file (avoid counting the same data
    twice and avoid infinite loops on broken symlinks). Errors
    reading a child are swallowed so a single permission
    failure does not nuke the whole report.
    """
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file() and not child.is_symlink():
                    total += child.stat().st_size
                elif child.is_file() and child.is_symlink():
                    try:
                        total += child.stat().st_size
                    except OSError:
                        pass
            except OSError:
                continue
    except OSError:
        pass
    return total


def sweep_download_dir(dry_run: bool = False) -> dict:
    """Walk ``MEDIA_SHUTTLE_DOWNLOAD_DIR`` and remove every
    artifact left by completed/failed/cancelled tasks. Used by
    the manual ``/leech cleanup`` Telegram command and by the
    supervisor's boot orphan sweep.

    The function only touches direct children of the configured
    download root. Each child is a single
    ``<sha1-seed>/tmp.part`` tree produced by
    ``materialize_path`` (or, for custom extensions, an entire
    plugin output directory). Path safety is delegated to
    ``cleanup_local_download`` which re-validates that each
    candidate lives under the resolved root.

    Returns a summary dict suitable for direct display to the
    operator: ``scanned``/``removed``/``skipped`` counters,
    total ``freed_bytes``, and a per-item list of paths and
    sizes (bounded by the number of direct children, which is
    typically dozens rather than millions, so it is safe to
    include in a Telegram reply).
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

    try:
        root_resolved = download_root.resolve()
    except OSError:
        return summary

    for child in sorted(download_root.iterdir()):
        summary["scanned"] += 1
        try:
            # Defense in depth: refuse anything that resolves
            # outside the download root. cleanup_local_download
            # does the same check internally, but failing here
            # lets us report the path in ``items`` instead of
            # silently skipping it.
            child.resolve().relative_to(root_resolved)
        except Exception:
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

        if cleanup_local_download(str(child)):
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
                    "reason": "cleanup_returned_false",
                }
            )

    return summary
