"""Retrieval and LM Studio client for the OfflineAI local backend."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import ctypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
LIGHTWEIGHT_MODEL = "qwen/qwen3-0.6b"
DEFAULT_MODEL = LIGHTWEIGHT_MODEL
QWEN_MODEL_SPEC = {
    "id": LIGHTWEIGHT_MODEL,
    "label": "Qwen3 0.6B (super-light)",
    "local_model_file": "qwen3-0.6b/Qwen3-0.6B-Q4_0.gguf",
    "context_window": 2048,
    "default_generation": {"temperature": 0.7, "top_p": 0.8, "max_output_tokens": 384},
}
LM_BASE = os.getenv("OFFLINEAI_LM_BASE", "http://127.0.0.1:1234/v1").rstrip("/")
DEFAULT_DB = Path(os.getenv("OFFLINEAI_DB_PATH", str(ROOT.parent / "data" / "documents.db")))
MAX_CONTEXT_CHARS = int(os.getenv("OFFLINEAI_MAX_CONTEXT_CHARS", "9000"))
MODEL_FOLDER = Path(os.getenv("OFFLINEAI_MODEL_FOLDER", str(ROOT.parent / "models"))).expanduser().resolve()
CONVERSATIONAL_MESSAGES = {
    "hi", "hello", "hey", "hiya", "how are you", "thanks", "thank you",
    "who are you", "what can you do", "help", "good morning", "good afternoon",
    "good evening", "what is this", "how does this work", "nice to meet you",
}
LIBRARY_REQUEST_TERMS = {
    "pdf", "pdfs", "book", "books", "manual", "manuals", "guide", "guides",
    "document", "documents", "library", "libraries", "source", "sources",
    "reference", "references", "according",
}


def _total_memory_bytes() -> int:
    """Read physical RAM without requiring psutil or another dependency."""
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return 0


def hardware_profile(available_model_ids: list[str] | None = None) -> dict[str, Any]:
    """Return safe defaults for the host without adding a hardware dependency."""
    logical_processors = os.cpu_count() or 1
    total_memory = _total_memory_bytes()
    memory_gb = total_memory / (1024 ** 3) if total_memory else 0
    low_resource = bool(memory_gb and memory_gb <= 6) or (logical_processors <= 4 and bool(memory_gb and memory_gb <= 8))
    available = set(available_model_ids or [])
    active_model = os.getenv("OFFLINEAI_ACTIVE_MODEL", "").strip() or DEFAULT_MODEL
    if low_resource:
        preferred = active_model if active_model in available else (LIGHTWEIGHT_MODEL if LIGHTWEIGHT_MODEL in available else DEFAULT_MODEL)
        return {
            "id": "low-resource",
            "label": "Low-resource mode",
            "message": "This computer has limited memory or CPU capacity. OfflineAI is using conservative settings to reduce freezing and paging.",
            "total_memory_gb": round(memory_gb, 1) if memory_gb else None,
            "logical_processors": logical_processors,
            "recommended_model": preferred,
            "parameters": {
                "temperature": 0.7 if preferred == "qwen/qwen3-0.6b" else 0.2,
                "topP": 0.8 if preferred == "qwen/qwen3-0.6b" else 0.9,
                "maxOutputTokens": 384,
                "maxContextTokens": 2048,
                "retrievalLimit": 2,
                "thinking": False,
            },
        }
    preferred = LIGHTWEIGHT_MODEL if LIGHTWEIGHT_MODEL in available else (active_model if active_model in available else DEFAULT_MODEL)
    return {
        "id": "standard",
        "label": "Standard mode",
        "message": "The computer appears suitable for the normal OfflineAI settings.",
        "total_memory_gb": round(memory_gb, 1) if memory_gb else None,
        "logical_processors": logical_processors,
        "recommended_model": preferred,
        "parameters": {
            "temperature": 0.2,
            "topP": 0.9,
            "maxOutputTokens": 700,
            "maxContextTokens": 4096,
            "retrievalLimit": 5,
            "thinking": True,
        },
    }


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def retrieval_decision(message: str) -> tuple[bool, str]:
    """Return whether a message has purposeful information intent."""
    normalized = re.sub(r"[^\w\s?]", "", (message or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip().rstrip("?")
    if not normalized:
        return False, "empty message"
    if normalized in CONVERSATIONAL_MESSAGES:
        return False, "short conversational message"
    if len(normalized.split()) <= 6 and normalized.startswith(("hi ", "hello ", "hey ", "thanks ", "thank you ", "good morning", "good afternoon", "good evening")):
        return False, "short conversational message"
    if normalized.startswith(("can you help", "could you help", "tell me about yourself")) and len(normalized.split()) <= 8:
        return False, "general conversational message"
    if not any(term in normalized.split() for term in LIBRARY_REQUEST_TERMS) and not any(phrase in normalized for phrase in ("look up in", "search the collection", "search my collection", "what do the documents say", "what do the books say")):
        return False, "no explicit library request"
    return True, "explicit library request"


def load_model_registry() -> dict[str, Any]:
    """Return the single model supported by this Linux installation.

    Qwen is built in deliberately.  A stale, missing, or partially updated
    model_registry.json must not make a running Qwen service appear empty.
    """
    path = ROOT / "model_registry.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [dict(item) for item in data.get("models", []) if item.get("id") == LIGHTWEIGHT_MODEL]
    except (OSError, json.JSONDecodeError):
        entries = []
    if not entries:
        entries = [dict(QWEN_MODEL_SPEC)]
    models = []
    for model in entries:
        local_file = Path(str(model.get("local_model_file", "")).replace("\\\\", "/"))
        resolved = local_file if local_file.is_absolute() else MODEL_FOLDER / local_file
        model["local_path"] = str(resolved)
        model["available"] = resolved.is_file()
        models.append(model)
    return {"folder": str(MODEL_FOLDER), "models": models}


def configured_model(model_id: str) -> dict[str, Any]:
    requested = str(model_id or "").strip()
    for item in load_model_registry()["models"]:
        if item.get("id") != requested:
            continue
        model = dict(item)
        local_file = Path(str(model.get("local_model_file", "")).replace("\\\\", "/"))
        model["local_path"] = str((MODEL_FOLDER / local_file).resolve())
        model["available"] = Path(model["local_path"]).is_file()
        return model
    raise ValueError(f"unsupported model; the installed build only supports {LIGHTWEIGHT_MODEL}")


def context_accounting(system_prompt: str, history: list[dict[str, Any]], context: list[dict[str, Any]],
                       max_context_tokens: int, skipped: bool, skip_reason: str | None,
                       current_message: str = "") -> dict[str, Any]:
    history_chars = sum(len(str(item.get("content", ""))) for item in history)
    retrieved_chars = sum(len(str(item.get("text", ""))) for item in context)
    system_chars = len(system_prompt)
    message_chars = len(current_message or "")
    used_tokens = (system_chars + history_chars + retrieved_chars + message_chars) // 4
    return {"estimated_used_tokens": used_tokens, "configured_context_limit": max_context_tokens,
            "percentage": round((used_tokens / max_context_tokens) * 100, 1) if max_context_tokens else 0,
            "retrieved_chars": retrieved_chars, "history_chars": history_chars,
            "system_prompt_chars": system_chars, "user_message_chars": message_chars,
            "library_search_skipped": skipped,
            "skip_reason": skip_reason}


class SearchIndex:
    """Read-only adapter for common SQLite document/chunk schemas.

    It first uses an FTS5 virtual table if one can be identified, then falls back
    to LIKE search. The adapter never creates, alters, or overwrites the worker DB.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"index database not found: {self.db_path}")
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _tables(con: sqlite3.Connection) -> list[str]:
        return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")]

    @staticmethod
    def _columns(con: sqlite3.Connection, table: str) -> list[str]:
        return [r[1] for r in con.execute(f"PRAGMA table_info({_quote_ident(table)})")]

    def _choose_table(self, con: sqlite3.Connection) -> tuple[str, list[str], bool]:
        tables = self._tables(con)
        fts = [t for t in tables if "fts" in t.lower()]
        candidates = fts + [t for t in tables if t not in fts and not t.startswith("sqlite_")]
        for table in candidates:
            cols = self._columns(con, table)
            text_cols = [c for c in cols if c.lower() in {"text", "content", "chunk_text", "body", "passage", "page_text"}]
            if text_cols:
                return table, cols, table in fts
        raise RuntimeError("no searchable text table found in SQLite database")

    @staticmethod
    def _find(cols: list[str], names: tuple[str, ...]) -> str | None:
        low = {c.lower(): c for c in cols}
        for n in names:
            if n in low:
                return low[n]
        return None

    @staticmethod
    def _tokens(query: str) -> list[str]:
        words = re.findall(r"[A-Za-z0-9_]{2,}", query.lower())
        stop = {"what", "where", "when", "which", "with", "from", "that", "this", "does", "have", "about", "into", "are", "the", "and", "for", "how", "can", "could", "would", "should", "tell", "please"}
        seen: set[str] = set()
        return [word for word in words if word not in stop and not (word in seen or seen.add(word))][:24]

    @staticmethod
    def _snippet(text: str, tokens: list[str], width: int = 900) -> str:
        clean = " ".join((text or "").split())
        if len(clean) <= width:
            return clean
        lower = clean.lower()
        positions = [lower.find(token) for token in tokens if lower.find(token) >= 0]
        start = max(0, (min(positions) if positions else 0) - 220)
        end = min(len(clean), start + width)
        return ("..." if start else "") + clean[start:end] + ("..." if end < len(clean) else "")

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 20))
        tokens = self._tokens(query)
        if not tokens:
            return []
        con = self._connect()
        try:
            tables = set(self._tables(con))
            if {"page_fts", "pages", "documents"}.issubset(tables):
                fts_query = " OR ".join(f'"{token}"' for token in tokens)
                rows = con.execute(
                    """SELECT p.text, d.rel_path, d.title, p.page_no
                       FROM page_fts AS f
                       JOIN pages AS p ON p.rowid = f.rowid
                       JOIN documents AS d ON d.id = p.document_id
                       WHERE page_fts MATCH ?
                       ORDER BY bm25(page_fts)
                       LIMIT ?""",
                    (fts_query, limit),
                ).fetchall()
                return [{
                    "text": self._snippet(str(row[0] or ""), tokens),
                    "source_filename": Path(str(row[1] or "")).name or str(row[2] or "unknown"),
                    "relative_path": str(row[1] or ""),
                    "pdf_page": row[3],
                } for row in rows if str(row[0] or "").strip()]

            table, cols, is_fts = self._choose_table(con)
            text_col = self._find(cols, ("text", "content", "chunk_text", "body", "passage", "page_text"))
            assert text_col
            filename = self._find(cols, ("source_filename", "filename", "file_name", "name"))
            relpath = self._find(cols, ("relative_path", "rel_path", "path", "source_path"))
            page = self._find(cols, ("pdf_page", "page_number", "page", "page_num"))
            params: list[Any] = []
            if is_fts:
                sql = f"SELECT rowid AS __rid, * FROM {_quote_ident(table)} WHERE {_quote_ident(table)} MATCH ? LIMIT ?"
                params = [query.replace('"', ' '), limit]
            else:
                sql = f"SELECT * FROM {_quote_ident(table)} WHERE {_quote_ident(text_col)} LIKE ? LIMIT ?"
                params = [f"%{query}%", limit]
            rows = con.execute(sql, params).fetchall()
            # FTS5 tables commonly contain only indexed text. Recover metadata
            # from the external-content table using the shared rowid.
            metadata_rows = {}
            if is_fts and not (filename or relpath or page):
                for candidate in self._tables(con):
                    if candidate == table or "fts" in candidate.lower():
                        continue
                    candidate_cols = self._columns(con, candidate)
                    candidate_text = self._find(candidate_cols, ("text", "content", "chunk_text", "body", "passage", "page_text"))
                    if candidate_text:
                        filename = filename or self._find(candidate_cols, ("source_filename", "filename", "file_name", "name"))
                        relpath = relpath or self._find(candidate_cols, ("relative_path", "rel_path", "path", "source_path"))
                        page = page or self._find(candidate_cols, ("pdf_page", "page_number", "page", "page_num"))
                        wanted = [c for c in (filename, relpath, page) if c]
                        select_cols = ["rowid AS __rid"] + wanted + [candidate_text]
                        try:
                            metadata_rows = {r["__rid"]: r for r in con.execute(f"SELECT {', '.join(_quote_ident(c) if c != 'rowid AS __rid' else c for c in select_cols)} FROM {_quote_ident(candidate)}")}
                            break
                        except sqlite3.Error:
                            continue
            out = []
            for row in rows:
                metadata = metadata_rows.get(row["__rid"], {}) if is_fts else row
                out.append({
                    "text": str((metadata[text_col] if text_col in metadata.keys() else row[text_col]) or ""),
                    "source_filename": str(metadata[filename] or "") if filename and filename in metadata.keys() else (str(row[filename] or "") if filename and filename in row.keys() else ""),
                    "relative_path": str(metadata[relpath] or "") if relpath and relpath in metadata.keys() else (str(row[relpath] or "") if relpath and relpath in row.keys() else ""),
                    "pdf_page": metadata[page] if page and page in metadata.keys() else (row[page] if page and page in row.keys() else None),
                })
            return out
        except sqlite3.Error as exc:
            raise RuntimeError(f"search failed: {exc}") from exc
        finally:
            con.close()


class LMStudioClient:
    def __init__(self, base_url: str = LM_BASE):
        self.base_url = base_url.rstrip("/")

    def models(self) -> dict[str, Any]:
        try:
            return self._request("GET", "/models")
        except Exception as exc:
            return {"data": [], "offline": True, "error": str(exc)}

    def chat(self, model: str, message: str, context: list[dict[str, Any]], system_prompt: str,
             history: list[dict[str, Any]] | None = None, temperature: float = 0.2,
             top_p: float = 0.9, max_output_tokens: int = 700,
             max_context_tokens: int = 2048, thinking: bool = True,
             timeout_seconds: int | None = None) -> dict[str, Any]:
        blocks = []
        for i, item in enumerate(context, 1):
            citation = f"filename={item['source_filename'] or 'unknown'}; relative_path={item['relative_path'] or 'unknown'}; pdf_page={item['pdf_page'] if item['pdf_page'] is not None else 'unknown'}"
            blocks.append(f"[Source {i}: {citation}]\n{item['text'][:2500]}")
        joined = "\n\n".join(blocks)[:min(MAX_CONTEXT_CHARS, max_context_tokens * 4)]
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-8:])
        messages.append({"role": "user", "content": f"Retrieved sources:\n{joined or '(none)'}\n\nQuestion:\n{message}"})
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "top_p": top_p, "max_tokens": max_output_tokens, "stream": False,
                   "chat_template_kwargs": {"enable_thinking": bool(thinking)}}
        result = self._request("POST", "/chat/completions", payload, timeout_seconds=timeout_seconds)
        try:
            message_result = result["choices"][0]["message"]
            raw_answer = str(message_result.get("content") or "")
            reasoning = str(message_result.get("reasoning_content") or message_result.get("thinking") or "")
            think_match = re.search(r"<think>\s*(.*?)\s*</think>", raw_answer, flags=re.IGNORECASE | re.DOTALL)
            if think_match:
                reasoning = reasoning or think_match.group(1).strip()
                raw_answer = (raw_answer[:think_match.start()] + raw_answer[think_match.end():]).strip()
            result["answer"] = raw_answer.strip()
            result["reasoning"] = reasoning.strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LM Studio returned a malformed chat response") from exc
        return result

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 timeout_seconds: int | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            timeout = timeout_seconds if timeout_seconds is not None else (180 if method == "POST" else 8)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise RuntimeError("LM Studio returned non-object JSON")
                return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise RuntimeError(f"LM Studio HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LM Studio unavailable: {exc}") from exc


def load_system_prompt() -> str:
    prompt = (ROOT / "system_prompt.txt").read_text(encoding="utf-8")
    # Keep the prompt portable: the Windows package uses E:\\PDF while the
    # Linux bundle points at its mounted USB/PDF directory.
    library_root = os.getenv("OFFLINEAI_PDF_ROOT", "E:\\PDF")
    return prompt.replace("E:\\PDF", library_root)
