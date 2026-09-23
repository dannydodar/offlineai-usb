"""Restart diagnostics readable even by an old, already-running backend."""
from __future__ import annotations
import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def report(stage: str, detail: str = '') -> None:
    path = ROOT / 'ui/config.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['restartDiagnostic'] = {'stage': stage, 'detail': detail[-600:], 'time': time.time()}
    temporary = path.with_suffix('.restart.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def owned_process(pid: int, targets: set[Path], proc: Path = Path('/proc')) -> bool:
    """Match complete arguments and owner, never an unchecked PID file."""
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        directory = proc / str(pid)
        if directory.stat().st_uid != os.getuid():
            return False
        args = (directory / 'cmdline').read_bytes().split(b'\0')
        cwd = (directory / 'cwd').resolve()
        return any((Path(os.fsdecode(arg)) if os.path.isabs(os.fsdecode(arg))
                    else cwd / os.fsdecode(arg)).resolve() in targets
                   for arg in args if arg)
    except (OSError, ValueError):
        return False


def stop() -> None:
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'offlineai'
    targets = {(ROOT / 'app/server.py').resolve(),
               (ROOT / 'runtime/llama.cpp/llama-server').resolve(),
               (cache / 'runtime/llama.cpp/llama-server').resolve()}
    pids = [int(entry.name) for entry in Path('/proc').iterdir()
            if entry.name.isdigit() and owned_process(int(entry.name), targets)]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any(owned_process(pid, targets) for pid in pids):
        time.sleep(.1)
    for pid in pids:
        if owned_process(pid, targets):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == '__main__':
    if sys.argv[1] == 'stop':
        stop()
    elif sys.argv[1] == 'report':
        report(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '')
