from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rag_backend import (DEFAULT_MODEL, LMStudioClient, SearchIndex, configured_model,
                         context_accounting, load_model_registry, load_system_prompt,
                         retrieval_decision)
from update_manager import apply_update, check_updates

# The portable assistant is intentionally local-only. Keep the bind address
# fixed even if a stale environment variable is present on the host laptop.
HOST = "127.0.0.1"
PORT = int(os.getenv("OFFLINEAI_PORT", "8765"))
ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = ROOT / "ui"
PDF_ROOT = Path(os.getenv("OFFLINEAI_PDF_ROOT", "E:\\PDF")).resolve()
index = SearchIndex()
lm = LMStudioClient()


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
            self._send(200, {"ok": True, "service": "offlineai-backend", "bind": HOST, "engine": os.getenv("OFFLINEAI_ENGINE", "LM Studio API"), "database": str(index.db_path), "library": os.getenv("OFFLINEAI_PDF_ROOT", "E:\\PDF")})
        elif route == "/models":
            # Expose only the intentionally configured local model registry.
            self._send(200, load_model_registry())
        elif route == "/config":
            config = load_model_registry()
            config["system_prompt"] = load_system_prompt()
            config["engine"] = os.getenv("OFFLINEAI_ENGINE", "LM Studio API")
            config["version"] = __import__("update_manager").current_version()
            self._send(200, config)
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
        if route != "/chat":
            self._send(404, {"error": "not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            message = str(body.get("message", "")).strip()
            if not message:
                raise ValueError("message is required")
            model = body.get("model", DEFAULT_MODEL)
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
