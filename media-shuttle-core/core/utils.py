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
