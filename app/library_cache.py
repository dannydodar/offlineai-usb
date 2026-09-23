"""Optional local cache for the read-only PDF library."""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {"status": "idle", "files": 0, "bytes": 0, "total_files": 0, "total_bytes": 0, "error": ""}
CACHE_CATALOG = ".offlineai-pdf-catalog.sqlite3"


def cache_root() -> Path:
    override = os.getenv("OFFLINEAI_PDF_CACHE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return (Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "OfflineAI" / "pdf-library").resolve()
    return (Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "offlineai" / "pdf-library").resolve()


def _marker(root: Path) -> Path:
    return root / ".offlineai-pdf-cache.json"


def active_root(source: Path) -> Path:
    root = cache_root()
    return root if _marker(root).is_file() else source


def active_catalog(source: Path, catalog: Path) -> Path:
    root = cache_root()
    cached = root / CACHE_CATALOG
    return cached if _marker(root).is_file() and cached.is_file() else catalog


def status(source: Path, catalog: Path | None = None) -> dict[str, Any]:
    root = cache_root()
    with _lock:
        current = dict(_state)
    marker_data: dict[str, Any] = {}
    if _marker(root).is_file():
        try:
            marker_data = json.loads(_marker(root).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker_data = {}
    return {
        **current,
        "source": str(source),
        "cache": str(root),
        "active": str(active_root(source)),
        "cached": bool(marker_data),
        "cached_files": marker_data.get("files", 0),
        "cached_bytes": marker_data.get("bytes", 0),
        "cached_catalog": (root / CACHE_CATALOG).is_file(),
        "catalog": str(catalog) if catalog else "",
    }


def _copy_worker(source: Path, destination: Path, catalog: Path | None) -> None:
    try:
        files = [path for path in source.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"]
        catalog_size = catalog.stat().st_size if catalog and catalog.is_file() else 0
        total_bytes = sum(path.stat().st_size for path in files) + catalog_size
        destination.mkdir(parents=True, exist_ok=True)
        marker = _marker(destination)
        marker.unlink(missing_ok=True)
        copied = copied_bytes = 0
        with _lock:
            _state.update(status="copying", files=0, bytes=0, total_files=len(files) + bool(catalog_size), total_bytes=total_bytes, error="")
        for source_file in files:
            target = destination / source_file.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
            copied += 1
            copied_bytes += source_file.stat().st_size
            with _lock:
                _state.update(files=copied, bytes=copied_bytes)
        if catalog and catalog.is_file():
            shutil.copy2(catalog, destination / CACHE_CATALOG)
            copied += 1
            copied_bytes += catalog_size
            with _lock:
                _state.update(files=copied, bytes=copied_bytes)
        marker.write_text(json.dumps({"files": copied, "bytes": copied_bytes}, indent=2), encoding="utf-8")
        with _lock:
            _state.update(status="complete", files=copied, bytes=copied_bytes, total_files=len(files) + bool(catalog_size), total_bytes=total_bytes, error="")
    except Exception as exc:
        with _lock:
            _state.update(status="error", error=str(exc))


def start(source: Path, catalog: Path | None = None) -> dict[str, Any]:
    destination = cache_root()
    if not source.is_dir():
        raise FileNotFoundError(f"PDF library folder not found: {source}")
    if catalog is not None and not catalog.is_file():
        raise FileNotFoundError(f"PDF catalog not found: {catalog}")
    if source.resolve() == destination:
        raise RuntimeError("the PDF source and local cache are the same folder")
    if destination in source.resolve().parents:
        raise RuntimeError("the local PDF cache cannot be inside the USB PDF library")
    with _lock:
        if _state.get("status") == "copying":
            return status(source)
        _state.update(status="starting", files=0, bytes=0, total_files=0, total_bytes=0, error="")
    threading.Thread(target=_copy_worker, args=(source, destination, catalog), name="offlineai-pdf-cache", daemon=True).start()
    return status(source, catalog)
