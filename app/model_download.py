from __future__ import annotations

import hashlib
import os
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MODEL_FOLDER = Path(os.getenv("OFFLINEAI_MODEL_FOLDER", str(ROOT / "models")))
CA_BUNDLE = Path(__file__).resolve().parent / "cacert.pem"

QWEN_MODEL = {
    "id": "qwen/qwen3-0.6b",
    "label": "Qwen3 0.6B (super-light)",
    "relative_path": "qwen3-0.6b/Qwen3-0.6B-Q4_0.gguf",
    "url": "https://huggingface.co/ggml-org/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q4_0.gguf?download=true",
    "sha256": "da2572f16c06133561ce56accaa822216f2391ef4d37fba427801cd6736417d4",
    "size_bytes": 428970080,
}

_lock = threading.Lock()
_state: dict[str, Any] = {
    "status": "idle",
    "model_id": QWEN_MODEL["id"],
    "label": QWEN_MODEL["label"],
    "bytes_downloaded": 0,
    "total_bytes": QWEN_MODEL["size_bytes"],
    "percent": 0,
    "message": "",
}


def _target() -> Path:
    return MODEL_FOLDER / Path(QWEN_MODEL["relative_path"])


def _snapshot() -> dict[str, Any]:
    with _lock:
        result = dict(_state)
    result["installed"] = _target().is_file()
    result["location"] = str(_target())
    return result


def catalog() -> list[dict[str, Any]]:
    target = _target()
    return [{
        "id": QWEN_MODEL["id"],
        "label": QWEN_MODEL["label"],
        "size_bytes": QWEN_MODEL["size_bytes"],
        "size_gb": round(QWEN_MODEL["size_bytes"] / (1024 ** 3), 2),
        "installed": target.is_file(),
        "downloadable": True,
    }]


def status() -> dict[str, Any]:
    return _snapshot()


def _set_state(**values: Any) -> None:
    with _lock:
        _state.update(values)


def _context() -> ssl.SSLContext:
    if CA_BUNDLE.is_file():
        return ssl.create_default_context(cafile=str(CA_BUNDLE))
    return ssl.create_default_context()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _download_worker() -> None:
    target = _target()
    temporary = target.with_name(f".{target.name}.download")
    target.parent.mkdir(parents=True, exist_ok=True)
    last_error = "download failed"
    for attempt in range(1, 4):
        try:
            _set_state(status="downloading", message=f"Downloading model (attempt {attempt} of 3)…", bytes_downloaded=0, percent=0)
            request = urllib.request.Request(QWEN_MODEL["url"], headers={
                "User-Agent": "OfflineAI-Model-Downloader/1.0",
                "Accept": "application/octet-stream",
            })
            with urllib.request.urlopen(request, timeout=45, context=_context()) as response, temporary.open("wb") as output:
                total = int(response.headers.get("Content-Length") or QWEN_MODEL["size_bytes"])
                downloaded = 0
                _set_state(total_bytes=total)
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    downloaded += len(block)
                    _set_state(bytes_downloaded=downloaded, total_bytes=total,
                               percent=round(min(100, downloaded * 100 / total), 1) if total else 0,
                               message=f"Downloading model… {downloaded / (1024 ** 2):.0f} MB of {total / (1024 ** 2):.0f} MB")
            if _sha256(temporary) != QWEN_MODEL["sha256"]:
                raise RuntimeError("downloaded model failed its SHA-256 integrity check")
            os.replace(temporary, target)
            _set_state(status="complete", bytes_downloaded=QWEN_MODEL["size_bytes"], total_bytes=QWEN_MODEL["size_bytes"], percent=100,
                       message="Model downloaded and verified. Restart OfflineAI to use it.")
            return
        except (OSError, RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < 3:
                _set_state(message=f"Download interrupted; retrying… ({last_error})")
    _set_state(status="error", message=f"Model download failed: {last_error}", percent=0)


def start_download() -> dict[str, Any]:
    current = _snapshot()
    if current["status"] == "downloading":
        return current
    target = _target()
    if target.is_file():
        if _sha256(target) == QWEN_MODEL["sha256"]:
            _set_state(status="complete", bytes_downloaded=QWEN_MODEL["size_bytes"], total_bytes=QWEN_MODEL["size_bytes"], percent=100,
                       message="Model is already downloaded and verified. Restart OfflineAI to use it.")
            return _snapshot()
    _set_state(status="starting", bytes_downloaded=0, total_bytes=QWEN_MODEL["size_bytes"], percent=0, message="Preparing model download…")
    threading.Thread(target=_download_worker, name="offlineai-model-download", daemon=True).start()
    return _snapshot()
