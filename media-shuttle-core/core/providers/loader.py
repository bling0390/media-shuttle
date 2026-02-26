from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any


def _modules_from_env() -> list[str]:
    raw = os.getenv("MEDIA_SHUTTLE_EXTRA_PROVIDER_MODULES", "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _modules_from_config(kind: str) -> list[str]:
    path = os.getenv("MEDIA_SHUTTLE_EXTRA_PROVIDER_CONFIG", "").strip()
    if not path:
        return []
    fp = Path(path)
    if not fp.exists():
        return []
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []

    common = data.get("modules", []) if isinstance(data, dict) else []
    scoped_key = f"{kind}_modules"
    scoped = data.get(scoped_key, []) if isinstance(data, dict) else []

    values = []
    for item in [*common, *scoped]:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
    return values


def _export_names(kind: str) -> tuple[str, str]:
    if kind == "parse":
        return "get_parse_providers", "PARSE_PROVIDERS"
    if kind == "download":
        return "get_download_providers", "DOWNLOAD_PROVIDERS"
    if kind == "upload":
        return "get_upload_providers", "UPLOAD_PROVIDERS"
    raise ValueError(f"unknown provider kind: {kind}")


def load_extra_providers(kind: str, mode: str, modules: list[str] | None = None) -> list[Any]:
    getter_name, list_name = _export_names(kind)
    module_names = list(dict.fromkeys([*(modules or []), *_modules_from_env(), *_modules_from_config(kind)]))

    providers: list[Any] = []
    for module_name in module_names:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            continue

        getter = getattr(mod, getter_name, None)
        if callable(getter):
            try:
                got = getter(mode)
                if isinstance(got, list):
                    providers.extend(got)
            except Exception:
                continue
            continue

        got = getattr(mod, list_name, None)
        if isinstance(got, list):
            providers.extend(got)

    return providers
