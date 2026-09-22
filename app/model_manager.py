from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MODEL_FOLDER = Path(os.getenv("OFFLINEAI_MODEL_FOLDER", str(ROOT / "models")))


def runtime_status() -> dict[str, Any]:
    """Inspect the local model runner instead of trusting stale environment flags."""
    base_url = os.getenv("OFFLINEAI_LM_BASE", "").strip().rstrip("/")
    if not base_url:
        return {"checked": False, "running": False, "model": "", "endpoint": ""}
    runner_url = base_url[:-3] if base_url.endswith("/v1") else base_url
    result: dict[str, Any] = {
        "checked": True,
        "running": False,
        "model": "",
        "endpoint": runner_url,
    }
    try:
        request = urllib.request.Request(runner_url + "/health", headers={"User-Agent": "OfflineAI/1.0"})
        with urllib.request.urlopen(request, timeout=1.0) as response:
            result["health"] = json.loads(response.read().decode("utf-8"))
        request = urllib.request.Request(base_url + "/models", headers={"User-Agent": "OfflineAI/1.0"})
        with urllib.request.urlopen(request, timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("data", []) if isinstance(payload, dict) else []
        if models and isinstance(models[0], dict):
            result["model"] = str(models[0].get("id", ""))
        result["running"] = True
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        result["error"] = str(exc)
    return result


def _registry() -> list[dict[str, Any]]:
    data = json.loads((ROOT / "app" / "model_registry.json").read_text(encoding="utf-8"))
    return [dict(item) for item in data.get("models", [])]


def _is_allowed(model_id: str) -> bool:
    return os.getenv("OFFLINEAI_MODEL_POLICY", "").strip().lower() != "lightweight" or model_id == "qwen/qwen3-0.6b"


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
    runtime = runtime_status()
    active = os.getenv("OFFLINEAI_ACTIVE_MODEL", "").strip()
    running_model = str(runtime.get("model", ""))
    if not active and running_model:
        active = running_model
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
        model_id = str(item.get("id", ""))
        running = model_id == running_model
        allowed = _is_allowed(model_id)
        result.append({
            "id": model_id,
            "label": str(item.get("label", item.get("id", ""))),
            "installed": installed,
            "available": installed,
            "allowed": allowed,
            "active": model_id == active,
            "running": running,
            "can_delete": installed and model_id != active and not running,
            "size_bytes": total_bytes,
            "files": managed_files,
            "location": str(folder),
            "path": str(path),
        })
    return result


def delete_model(model_id: str) -> dict[str, Any]:
    requested = str(model_id or "").strip()
    item = next((entry for entry in _registry() if str(entry.get("id", "")) == requested), None)
    if item is None:
        raise ValueError("that model is not managed by OfflineAI")
    active = os.getenv("OFFLINEAI_ACTIVE_MODEL", "").strip()
    runtime = runtime_status()
    running_model = str(runtime.get("model", ""))
    if requested == active or requested == running_model:
        raise RuntimeError("the active or running model cannot be deleted while OfflineAI is running")
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
