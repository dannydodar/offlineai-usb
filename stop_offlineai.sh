#!/usr/bin/env bash
# Stop only the OfflineAI processes recorded by this installation.
set -u
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "${OFFLINEAI_PYTHON:-python3}" "$ROOT/app/linux_recovery.py" stop
