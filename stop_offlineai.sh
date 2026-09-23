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
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
}

stop_pid_file "$STATE_ROOT/backend.pid"
stop_pid_file "$STATE_ROOT/runner.pid"
echo "OfflineAI stopped."
