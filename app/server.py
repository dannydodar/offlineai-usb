from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rag_backend import (BACKEND_BUILD, DEFAULT_MODEL, LMStudioClient, SearchIndex, configured_model,
                         LIGHTWEIGHT_MODEL, context_accounting, hardware_profile, load_model_registry,
                         load_system_prompt, retrieval_decision)
from model_download import catalog as model_download_catalog, start_download, status as model_download_status
from model_manager import delete_model, inventory as model_inventory, runtime_status
from update_manager import apply_update, check_updates, current_version

# The portable assistant is intentionally local-only. Keep the bind address
# fixed even if a stale environment variable is present on the host laptop.
HOST = "127.0.0.1"
PORT = int(os.getenv("OFFLINEAI_PORT", "8765"))
ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = ROOT / "ui"
PDF_ROOT = Path(os.getenv("OFFLINEAI_PDF_ROOT", "E:\\PDF")).resolve()
index = SearchIndex()
lm = LMStudioClient()
INSTANCE_ID = uuid.uuid4().hex
STARTED_AT = time.time()


def _short_debug_error(value: object, limit: int = 180) -> str:
    clean = " ".join(str(value or "").split())
    return clean[:limit] + ("…" if len(clean) > limit else "")


def _debug_report() -> dict:
    """Return a compact, user-readable local diagnostic instead of a log dump."""
    lines = [f"OfflineAI v{current_version()}"]
    lines.append(f"Backend build: {BACKEND_BUILD}")
    passed = True
    runtime = runtime_status()
    runner_model = str(runtime.get("model") or "").strip()
    if runtime.get("running"):
        lines.append(f"Runner: OK ({runner_model or 'local runner responded'})")
    else:
        passed = False
        lines.append(f"Runner: FAIL ({_short_debug_error(runtime.get('error') or 'not responding')})")

    try:
        registry = load_model_registry()
        models = registry.get("models", [])
        qwen = next((item for item in models if item.get("id") == LIGHTWEIGHT_MODEL), None)
        if qwen:
            file_state = "file present" if qwen.get("available") else "file not visible to backend"
            lines.append(f"Registry: Qwen3 configured ({file_state})")
        else:
            passed = False
            lines.append("Registry: FAIL (Qwen3 missing from backend registry)")
    except Exception as exc:
        passed = False
        lines.append(f"Registry: FAIL ({_short_debug_error(exc)})")

    if runtime.get("running"):
        try:
            runner_models = lm.models()
            runner_entries = runner_models.get("data", []) if isinstance(runner_models, dict) else []
            runner_ids = [str(item.get("id", "")).strip() for item in runner_entries
                          if isinstance(item, dict) and str(item.get("id", "")).strip()]
            lines.append(f"Runner model IDs: {', '.join(runner_ids[:4]) or 'none reported'}")
            api_model = lm.probe(LIGHTWEIGHT_MODEL)
            lines.append(f"Model handshake: OK (Qwen ID mapped to {api_model})")
            lines.append("Chat probe: OK (runner accepted a tiny local request)")
        except Exception as exc:
            passed = False
            lines.append(f"Chat probe: FAIL ({_short_debug_error(exc)})")
    else:
        lines.append("Chat probe: SKIPPED (runner failed above)")
    return {"ok": passed, "backendBuild": BACKEND_BUILD, "summary": "\n".join(lines)}


def _restart_launcher() -> None:
    """Start the portable launcher after the current backend has answered."""
    port = os.getenv("OFFLINEAI_PORT", "8765")
    if os.name != "nt":
        launcher = ROOT / "start_offlineai.sh"
        if not launcher.is_file():
            raise RuntimeError("Linux launcher not found; restart OfflineAI manually")
        command = ["bash", str(launcher), "--port", port]
        subprocess.Popen(command, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, start_new_session=True)
        return
    launcher = ROOT / "start_usb.ps1"
    if not launcher.is_file():
        raise RuntimeError("portable launcher not found; restart OfflineAI manually")
    try:
        launcher_text = launcher.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        launcher_text = ""
    supports_qwen = "qwen3" in launcher_text
    if not supports_qwen:
        raise RuntimeError("the USB launcher is too old to restart lightweight mode; install the latest update and relaunch OfflineAI")
    model = "qwen3"
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher), "-Model", model, "-Port", port]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(command, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)


def _add_source_references(answer: str, sources: list[dict]) -> str:
    references = []
    seen = set()
    for item in sources:
        filename = item.get("source_filename") or "unknown document"
        page = item.get("pdf_page")
        label = f"{filename}, p. {page}" if page is not None else filename
        if label not in seen:
            seen.add(label)
            references.append(f"- [{label}]")
    return answer.rstrip() + ("\n\nSources used:\n" + "\n".join(references) if references else "")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, data: dict):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path):
        resolved = path.resolve()
        if not path.exists() or not path.is_file() or UI_ROOT.resolve() not in resolved.parents:
            self._send(404, {"error": "not found"})
            return
        content_types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_pdf(self, relative_path: str):
        candidate = Path(relative_path)
        if not candidate.is_absolute():
            candidate = PDF_ROOT / candidate
        resolved = candidate.resolve()
        if PDF_ROOT not in resolved.parents or resolved.suffix.lower() != ".pdf":
            self._send(403, {"error": "source is outside the local PDF library"})
            return
        if not resolved.exists() or not resolved.is_file():
            self._send(404, {"error": "PDF source not found"})
            return
        raw = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", "inline")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path[4:] if parsed.path.startswith("/api/") else parsed.path
        if route in {"", "/", "/index.html"}:
            self._send_file(UI_ROOT / "index.html")
        elif route in {"/styles.css", "/app.js", "/config.json"}:
            self._send_file(UI_ROOT / route.lstrip("/"))
        elif route == "/health":
            self._send(200, {"ok": True, "service": "offlineai-backend", "bind": HOST,
                             "instance_id": INSTANCE_ID, "started_at": STARTED_AT, "pid": os.getpid(),
                             "engine": os.getenv("OFFLINEAI_ENGINE", "LM Studio API"),
                             "active_model": os.getenv("OFFLINEAI_ACTIVE_MODEL", ""),
                             "backend_build": BACKEND_BUILD, "runner": runtime_status(), "database": str(index.db_path),
                             "library": os.getenv("OFFLINEAI_PDF_ROOT", "E:\\PDF")})
        elif route == "/debug":
            self._send(200, _debug_report())
        elif route == "/models":
            # Expose only the intentionally configured local model registry.
            self._send(200, load_model_registry())
        elif route == "/models/manage":
            self._send(200, {"models": model_inventory(), "runtime": runtime_status()})
        elif route == "/config":
            config = load_model_registry()
            config["system_prompt"] = load_system_prompt()
            config["engine"] = os.getenv("OFFLINEAI_ENGINE", "LM Studio API")
            config["version"] = __import__("update_manager").current_version()
            config["backend_build"] = BACKEND_BUILD
            config["model_policy"] = os.getenv("OFFLINEAI_MODEL_POLICY", "normal")
            config["hardware"] = hardware_profile([item.get("id", "") for item in config.get("models", [])])
            config["downloadable_models"] = model_download_catalog()
            config["runtime"] = runtime_status()
            self._send(200, config)
        elif route == "/model/download/status":
            self._send(200, model_download_status())
        elif route == "/source":
            qs = parse_qs(parsed.query)
            self._send_pdf(qs.get("path", [""])[0])
        elif route == "/update/check":
            self._send(200, check_updates())
        elif route == "/search":
            qs = parse_qs(parsed.query)
            try:
                results = index.search(qs.get("q", [""])[0], int(qs.get("limit", [5])[0]))
                self._send(200, {"query": qs.get("q", [""])[0], "results": results})
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                self._send(503, {"error": str(exc), "results": []})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        route = urlparse(self.path).path
        route = route[4:] if route.startswith("/api/") else route
        if route == "/update/apply":
            self._send(200, apply_update())
            return
        if route == "/model/download":
            self._send(200, start_download())
            return
        if route == "/models/delete":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                self._send(200, delete_model(body.get("model", "")))
            except (json.JSONDecodeError, ValueError) as exc:
                self._send(400, {"ok": False, "error": str(exc)})
            except FileNotFoundError as exc:
                self._send(404, {"ok": False, "error": str(exc)})
            except RuntimeError as exc:
                self._send(409, {"ok": False, "error": str(exc)})
            except OSError as exc:
                self._send(503, {"ok": False, "error": f"model could not be deleted: {exc}"})
            return
        if route == "/restart":
            try:
                if os.name != "nt":
                    if not (ROOT / "start_offlineai.sh").is_file():
                        raise RuntimeError("Linux launcher not found; restart OfflineAI manually")
                else:
                    if not (ROOT / "start_usb.ps1").is_file():
                        raise RuntimeError("portable launcher not found; restart OfflineAI manually")
                    if os.getenv("OFFLINEAI_MODEL_POLICY", "").strip().lower() == "lightweight":
                        launcher_text = (ROOT / "start_usb.ps1").read_text(encoding="utf-8", errors="ignore")
                        if "qwen3" not in launcher_text:
                            raise RuntimeError("the USB launcher is too old to restart lightweight mode; install the latest update and relaunch OfflineAI")
                self._send(200, {"ok": True, "restartRequired": True, "message": "OfflineAI is restarting. This page will refresh shortly."})
                threading.Timer(0.8, _restart_launcher).start()
            except (OSError, RuntimeError) as exc:
                self._send(503, {"ok": False, "error": str(exc)})
            return
        if route != "/chat":
            self._send(404, {"error": "not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            message = str(body.get("message", "")).strip()
            if not message:
                raise ValueError("message is required")
            # This installation is intentionally Qwen-only. Ignore stale
            # browser/model IDs entirely so chat cannot fail in registry
            # validation before it reaches the local runner.
            model = LIGHTWEIGHT_MODEL
            model_config = configured_model(model)
            parameters = body.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError("parameters must be an object")
            temperature = float(body.get("temperature", parameters.get("temperature", model_config["default_generation"]["temperature"])))
            top_p = float(body.get("top_p", parameters.get("topP", parameters.get("top_p", model_config["default_generation"]["top_p"]))))
            max_output_tokens = int(body.get("max_output_tokens", parameters.get("maxOutputTokens", parameters.get("max_output_tokens", model_config["default_generation"]["max_output_tokens"]))))
            max_context_tokens = int(body.get("max_context_tokens", parameters.get("maxContextTokens", parameters.get("max_context_tokens", model_config["context_window"]))))
            retrieval_limit = int(body.get("retrieval_limit", parameters.get("retrievalLimit", 5)))
            if not 0 <= temperature <= 2 or not 0 < top_p <= 1 or max_output_tokens < 1 or max_context_tokens < 128:
                raise ValueError("invalid generation or context parameters")
            history = body.get("history", [])
            if not isinstance(history, list):
                raise ValueError("history must be a list")
            history = [item for item in history if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and str(item.get("content", "")).strip()][-8:]
            search_enabled = bool(body.get("searchLibrary", True))
            should_search, reason = retrieval_decision(message) if search_enabled else (False, "library search disabled by user")
            if should_search and retrieval_limit <= 0:
                should_search, reason = False, "retrieval limit is zero"
            context = index.search(message, retrieval_limit) if should_search else []
            system_prompt = load_system_prompt()
            thinking = bool(body.get("thinking", True))
            started = time.perf_counter()
            result = lm.chat(model, message, context, system_prompt, history=history,
                             temperature=temperature, top_p=top_p,
                             max_output_tokens=max_output_tokens,
                             max_context_tokens=max_context_tokens, thinking=thinking)
            elapsed_seconds = max(0.001, time.perf_counter() - started)
            accounting = context_accounting(system_prompt, history, context, max_context_tokens,
                                            not should_search, None if should_search else reason, message)
            sources = [{**item, "title": item.get("source_filename"), "path": item.get("relative_path"), "snippet": item.get("text")} for item in context]
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            timings = result.get("timings") if isinstance(result.get("timings"), dict) else {}
            prompt_tokens = usage.get("prompt_tokens", timings.get("prompt_n"))
            output_tokens = usage.get("completion_tokens", timings.get("predicted_n"))
            tokens_per_second = timings.get("predicted_per_second")
            if tokens_per_second is None and output_tokens is not None:
                tokens_per_second = float(output_tokens) / elapsed_seconds
            performance = {"elapsed_seconds": round(elapsed_seconds, 2), "prompt_tokens": prompt_tokens,
                           "output_tokens": output_tokens, "tokens_per_second": round(float(tokens_per_second), 1) if tokens_per_second is not None else None,
                           "thinking": thinking}
            self._send(200, {"answer": result["answer"], "message": _add_source_references(result["answer"], context), "model": model,
                             "reasoning": result.get("reasoning", ""), "performance": performance,
                             "sources": sources, "searchPerformed": bool(context),
                             "retrieval": {"performed": bool(context), "skipped": not bool(context), "reason": None if context else reason},
                             "context": accounting, "context_accounting": accounting, "config": model_config})
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, {"error": str(exc)})
        except (FileNotFoundError, RuntimeError) as exc:
            self._send(503, {"error": str(exc)})

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    print(f"OfflineAI backend listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
