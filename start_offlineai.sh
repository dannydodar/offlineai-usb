#!/usr/bin/env bash
# OfflineAI Linux launcher. It is deliberately local-only and CPU-only.
set -u

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${OFFLINEAI_PYTHON:-python3}"
PORT="${OFFLINEAI_PORT:-8765}"
RUNNER_PORT="${OFFLINEAI_RUNNER_PORT:-1235}"
OPEN_BROWSER=1
RUNTIME_VERSION="b10936"
MODEL_ID="qwen/qwen3-0.6b"
MODEL_FILE="Qwen3-0.6B-Q4_0.gguf"
MODEL_SHA256="da2572f16c06133561ce56accaa822216f2391ef4d37fba427801cd6736417d4"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --port) PORT="${2:-$PORT}"; shift 2 ;;
        --runner-port) RUNNER_PORT="${2:-$RUNNER_PORT}"; shift 2 ;;
        --no-browser) OPEN_BROWSER=0; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

die() { echo "OfflineAI could not start: $*" >&2; exit 1; }

command -v "$PYTHON" >/dev/null 2>&1 || die "Python 3 is not installed. Install python3, then run this launcher again."
[ -f "$ROOT/app/server.py" ] || die "the OfflineAI files are incomplete at $ROOT"
[ -f "$ROOT/runtime/llama.cpp/llama-server" ] || die "the Linux llama.cpp runner is missing from $ROOT/runtime"

STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/offlineai"
CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/offlineai"
MODEL_CACHE="$CACHE_ROOT/models/qwen3-0.6b"
MODEL_SOURCE="$ROOT/models/qwen3-0.6b/$MODEL_FILE"
MODEL_TARGET="$MODEL_CACHE/$MODEL_FILE"
RUNTIME_CACHE="$CACHE_ROOT/runtime/llama.cpp"
RUNNER_SOURCE="$ROOT/runtime/llama.cpp"
RUNNER="$RUNTIME_CACHE/llama-server"
LOG_ROOT="$STATE_ROOT/logs"
mkdir -p "$MODEL_CACHE" "$RUNTIME_CACHE" "$LOG_ROOT"

# This installation is intentionally Qwen-only. Remove old Gemma caches so
# stale files cannot be selected or consume the laptop's limited storage.
for legacy_dir in "$CACHE_ROOT/models/gemma-4-E2B-it-GGUF" "$CACHE_ROOT/models/gemma-4-E4B-it-GGUF"; do
    if [ -d "$legacy_dir" ]; then rm -rf -- "$legacy_dir"; fi
done

bash "$ROOT/stop_offlineai.sh" >/dev/null 2>&1 || true

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    else
        echo ""
    fi
}

if [ ! -f "$MODEL_TARGET" ] || [ "$(sha256_file "$MODEL_TARGET")" != "$MODEL_SHA256" ]; then
    [ -f "$MODEL_SOURCE" ] || die "the Qwen3 model is missing from the USB package"
    echo "Copying the lightweight AI model to this laptop (about 410 MB; one-time setup)…"
    cp -f "$MODEL_SOURCE" "$MODEL_TARGET" || die "could not copy the model to $MODEL_TARGET"
fi
MODEL_HASH="$(sha256_file "$MODEL_TARGET")"
[ -z "$MODEL_HASH" ] || [ "$MODEL_HASH" = "$MODEL_SHA256" ] || die "the cached model failed its integrity check"

if [ ! -x "$RUNNER" ] || [ ! -f "$RUNTIME_CACHE/.runtime-version" ] || [ "$(cat "$RUNTIME_CACHE/.runtime-version" 2>/dev/null)" != "$RUNTIME_VERSION" ]; then
    echo "Copying the CPU runner to this laptop’s local cache…"
    cp -a "$RUNNER_SOURCE"/. "$RUNTIME_CACHE"/ || die "could not copy the Linux CPU runner to the laptop cache"
    printf '%s\n' "$RUNTIME_VERSION" > "$RUNTIME_CACHE/.runtime-version"
fi
chmod +x "$RUNNER" 2>/dev/null || true
[ -x "$RUNNER" ] || die "the Linux CPU runner is not executable"
export LD_LIBRARY_PATH="$RUNTIME_CACHE${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

wait_http() {
    local url="$1"
    local attempts="${2:-120}"
    local n=0
    while [ "$n" -lt "$attempts" ]; do
        if "$PYTHON" - "$url" <<'PY'
import sys
import urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        raise SystemExit(0 if 200 <= response.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
        then return 0; fi
        n=$((n + 1))
        sleep 1
    done
    return 1
}

wait_port_free() {
    local port="$1"
    local attempts="${2:-15}"
    local n=0
    while [ "$n" -lt "$attempts" ]; do
        if "$PYTHON" - "$port" <<'PY'
import socket, sys
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
raise SystemExit(0)
PY
        then return 0; fi
        n=$((n + 1))
        sleep 1
    done
    return 1
}

if ! wait_port_free "$RUNNER_PORT" 15; then
    die "the local runner port $RUNNER_PORT is still occupied after stopping OfflineAI"
fi
if ! wait_port_free "$PORT" 15; then
    die "the OfflineAI port $PORT is still occupied after stopping OfflineAI"
fi

echo "Starting the local CPU AI runner…"
nohup "$RUNNER" \
    --model "$MODEL_TARGET" \
    --alias "$MODEL_ID" \
    --host 127.0.0.1 \
    --port "$RUNNER_PORT" \
    --ctx-size 2048 \
    --threads 4 \
    --threads-batch 4 \
    --n-gpu-layers 0 \
    --parallel 1 \
    >"$LOG_ROOT/runner.log" 2>&1 &
RUNNER_PID=$!
printf '%s\n' "$RUNNER_PID" > "$STATE_ROOT/runner.pid"
if ! wait_http "http://127.0.0.1:$RUNNER_PORT/health" 180; then
    echo "The AI runner did not become ready. Its log is: $LOG_ROOT/runner.log" >&2
    tail -n 30 "$LOG_ROOT/runner.log" 2>/dev/null || true
    bash "$ROOT/stop_offlineai.sh" >/dev/null 2>&1 || true
    exit 1
fi

PDF_ROOT="$ROOT/PDF"
for candidate in "$ROOT/../../PDF" "$ROOT/../PDF" "$ROOT/PDF"; do
    if [ -d "$candidate" ]; then PDF_ROOT="$candidate"; break; fi
done
DB_PATH="$ROOT/worker-pdf/pdf_catalog.sqlite3"

export OFFLINEAI_PORT="$PORT"
export OFFLINEAI_LM_BASE="http://127.0.0.1:$RUNNER_PORT/v1"
export OFFLINEAI_ENGINE="llama.cpp CPU (Linux; Qwen3 0.6B cached on this PC)"
export OFFLINEAI_ACTIVE_MODEL="$MODEL_ID"
export OFFLINEAI_MODEL_POLICY="lightweight"
export OFFLINEAI_MODEL_FOLDER="$CACHE_ROOT/models"
export OFFLINEAI_PDF_ROOT="$PDF_ROOT"
export OFFLINEAI_DB_PATH="$DB_PATH"

echo "Starting the local OfflineAI web interface…"
nohup "$PYTHON" -u "$ROOT/app/server.py" \
    >"$LOG_ROOT/backend.log" 2>&1 &
BACKEND_PID=$!
printf '%s\n' "$BACKEND_PID" > "$STATE_ROOT/backend.pid"
if ! wait_http "http://127.0.0.1:$PORT/api/health" 30; then
    echo "The OfflineAI interface did not become ready. Its log is: $LOG_ROOT/backend.log" >&2
    tail -n 30 "$LOG_ROOT/backend.log" 2>/dev/null || true
    bash "$ROOT/stop_offlineai.sh" >/dev/null 2>&1 || true
    exit 1
fi

# A stale process must never make the launcher report success. Confirm that
# this installation's backend, rather than an older process on the port, is
# serving the build marker expected by the current package.
if ! "$PYTHON" - "http://127.0.0.1:$PORT/api/health" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raise SystemExit(0 if payload.get("backend_build") == "runner-diagnostics-v2" else 1)
except Exception:
    raise SystemExit(1)
PY
then
    echo "The OfflineAI port is responding, but the installed backend build is not current." >&2
    bash "$ROOT/stop_offlineai.sh" >/dev/null 2>&1 || true
    exit 1
fi

URL="http://127.0.0.1:$PORT/"
echo "OfflineAI is ready at $URL"
echo "Logs are in $LOG_ROOT"
if [ "$OPEN_BROWSER" = "1" ]; then
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    elif command -v firefox >/dev/null 2>&1; then
        firefox "$URL" >/dev/null 2>&1 &
    fi
fi
