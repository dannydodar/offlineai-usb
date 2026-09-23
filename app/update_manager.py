from __future__ import annotations

import json
import os
import shutil
import ssl
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "version.json"
UPDATE_CONFIG = ROOT / "update_config.json"
CA_BUNDLE = Path(__file__).resolve().parent / "cacert.pem"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _config() -> dict[str, Any]:
    data = _read_json(UPDATE_CONFIG) if UPDATE_CONFIG.exists() else {}
    repository = str(os.getenv("OFFLINEAI_UPDATE_REPO", data.get("repository", ""))).strip().rstrip("/")
    return {"repository": repository, "branch": str(data.get("branch", "main"))}


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in str(value).lstrip("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0])[:3])


def _fetch(url: str, timeout: int = 15) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "OfflineAI-Updater/1.0", "Accept": "application/json"})
    # The bundled Python runtime may not know where the host OS certificate
    # store is. Ship a Mozilla CA bundle so HTTPS remains verified on fresh
    # laptops without relying on LM Studio or a system Python installation.
    context = ssl.create_default_context(cafile=str(CA_BUNDLE)) if CA_BUNDLE.is_file() else ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def current_version() -> str:
    try:
        return str(_read_json(VERSION_FILE).get("version", "0.0.0"))
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def check_updates() -> dict[str, Any]:
    config = _config()
    current = current_version()
    repository = config["repository"]
    result: dict[str, Any] = {"currentVersion": current, "repository": repository, "branch": config["branch"], "updateAvailable": False}
    if not repository:
        result.update({"ok": False, "message": "Update repository is not configured yet."})
        return result
    try:
        raw_url = f"https://raw.githubusercontent.com/{repository}/{config['branch']}/version.json"
        remote = json.loads(_fetch(raw_url).decode("utf-8"))
        latest = str(remote.get("version", "0.0.0"))
        result.update({"ok": True, "latestVersion": latest, "updateAvailable": _version_tuple(latest) > _version_tuple(current), "message": "Update check complete."})
        return result
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        result.update({"ok": False, "message": f"Update check failed: {exc}"})
        return result


def apply_update() -> dict[str, Any]:
    checked = check_updates()
    if not checked.get("ok"):
        return checked
    if not checked.get("updateAvailable"):
        checked["message"] = "OfflineAI is already up to date."
        return checked

    repository = str(checked["repository"])
    branch = str(checked["branch"])
    backup_root = ROOT / "update-backups" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    preserve = {"models", "runtime", "logs", "worker-pdf/pdf_catalog.sqlite3", "update-backups"}
    # Keep the launcher and helper scripts in sync with the backend. This is
    # important because the Restart button runs the root launcher, while the
    # app itself is updated from the app/ directory.
    allow_files = {
        "version.json", "update_config.json", "README.md",
        "start_usb.ps1", "start_usb.cmd",
        "stop_usb.ps1", "stop_usb.cmd",
        "offload_models.ps1",
        "start_offlineai.sh", "stop_offlineai.sh", "install_offlineai_desktop.sh", "Start OfflineAI.desktop", "README-LINUX.md",
    }
    allow_dirs = {"app", "ui", "worker-pdf"}
    try:
        archive = _fetch(f"https://github.com/{repository}/archive/refs/heads/{branch}.zip", timeout=60)
        with tempfile.TemporaryDirectory(prefix="offlineai-update-") as temp:
            archive_path = Path(temp) / "update.zip"
            archive_path.write_bytes(archive)
            extract_root = Path(temp) / "extract"
            with zipfile.ZipFile(archive_path) as zipped:
                zipped.extractall(extract_root)
            roots = [path for path in extract_root.iterdir() if path.is_dir()]
            if len(roots) != 1:
                raise RuntimeError("Update archive has an unexpected layout.")
            source_root = roots[0]
            backup_root.mkdir(parents=True, exist_ok=True)
            for name in allow_files:
                source = source_root / name
                destination = ROOT / name
                if source.is_file():
                    if destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(destination, backup_root / name)
                    shutil.copy2(source, destination)
            for name in allow_dirs:
                source = source_root / name
                destination = ROOT / name
                if not source.is_dir():
                    continue
                for source_file in source.rglob("*"):
                    if not source_file.is_file():
                        continue
                    relative = source_file.relative_to(source_root)
                    target = ROOT / relative
                    if str(relative).replace("\\", "/") in preserve:
                        continue
                    if target.exists():
                        backup_target = backup_root / relative
                        backup_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, target)
        return {**checked, "ok": True, "applied": True, "restartRequired": True, "message": "Update installed. Restart OfflineAI to load it."}
    except (OSError, urllib.error.URLError, TimeoutError, zipfile.BadZipFile, RuntimeError) as exc:
        return {**checked, "ok": False, "applied": False, "message": f"Update failed: {exc}"}
