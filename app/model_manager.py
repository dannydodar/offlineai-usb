from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MODEL_FOLDER = Path(os.getenv("OFFLINEAI_MODEL_FOLDER", str(ROOT / "models")))


def _registry() -> list[dict[str, Any]]:
    data = json.loads((ROOT / "app" / "model_registry.json").read_text(encoding="utf-8"))
    return [dict(item) for item in data.get("models", [])]


def _model_path(item: dict[str, Any]) -> Path:
    relative = Path(str(item.get("local_model_file", "")))
    if relative.is_absolute():
        raise ValueError("absolute model paths are not managed by the UI")
    root = MODEL_FOLDER.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError("model path is outside the configured model folder")
    return path


def inventory() -> list[dict[str, Any]]:
    active = os.getenv("OFFLINEAI_ACTIVE_MODEL", "").strip()
    result = []
    for item in _registry():
        path = _model_path(item)
        folder = path.parent
        managed_files = []
        total_bytes = 0
        if folder.is_dir():
            for candidate in folder.iterdir():
                if candidate.is_file() and (candidate.suffix.lower() == ".gguf" or candidate.name == ".offlineai-source.json"):
                    managed_files.append(candidate.name)
                    total_bytes += candidate.stat().st_size
        installed = path.is_file()
        result.append({
            "id": str(item.get("id", "")),
            "label": str(item.get("label", item.get("id", ""))),
            "installed": installed,
            "active": str(item.get("id", "")) == active,
            "can_delete": installed and str(item.get("id", "")) != active,
            "size_bytes": total_bytes,
            "files": managed_files,
            "location": str(folder),
        })
    return result


def delete_model(model_id: str) -> dict[str, Any]:
    requested = str(model_id or "").strip()
    item = next((entry for entry in _registry() if str(entry.get("id", "")) == requested), None)
    if item is None:
        raise ValueError("that model is not managed by OfflineAI")
    active = os.getenv("OFFLINEAI_ACTIVE_MODEL", "").strip()
    if requested == active:
        raise RuntimeError("the active model cannot be deleted while OfflineAI is running")
    path = _model_path(item)
    folder = path.parent
    root = MODEL_FOLDER.resolve()
    if folder == root or root not in folder.parents:
        raise ValueError("refusing to delete the configured model root")
    if not path.exists():
        raise FileNotFoundError("model is not installed in the current model cache")
    removed = []
    for candidate in folder.iterdir():
        if candidate.is_file() and (candidate.suffix.lower() == ".gguf" or candidate.name == ".offlineai-source.json"):
            candidate.unlink()
            removed.append(candidate.name)
    try:
        folder.rmdir()
    except OSError:
        pass
    return {"ok": True, "model": requested, "removed": removed, "models": inventory()}
