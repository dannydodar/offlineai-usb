#!/usr/bin/env bash
# Stop only the OfflineAI processes recorded by this installation.
set -u
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
STATE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/offlineai"

stop_pid_file() {
    local file="$1"
    [ -f "$file" ] || return 0
    local pid
    pid="$(cat "$file" 2>/dev/null || true)"
    terminate_pid "$pid"
    rm -f "$file"
}

terminate_pid() {
    local pid="$1"
    [ -n "$pid" ] || return 0
    [ "$pid" = "$$" ] && return 0
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8; do
            kill -0 "$pid" 2>/dev/null || return 0
            sleep 1
        done
        kill -9 "$pid" 2>/dev/null || true
    fi
}

stop_matching_processes() {
    local pattern="$1"
    command -v pgrep >/dev/null 2>&1 || return 0
    local pid
    for pid in $(pgrep -f -- "$pattern" 2>/dev/null || true); do
        terminate_pid "$pid"
    done
}

stop_pid_file "$STATE_ROOT/backend.pid"
stop_pid_file "$STATE_ROOT/runner.pid"
# PID files can be stale after a crash or an interrupted restart. Remove any
# remaining process belonging to this exact installation so a new backend
# cannot silently fail to bind while an old one keeps serving the UI.
stop_matching_processes "$ROOT/app/server.py"
stop_matching_processes "$ROOT/runtime/llama.cpp/llama-server"
echo "OfflineAI stopped."
