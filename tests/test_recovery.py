"""Regression checks for updating a legacy backend and serving the new API."""
import io
import os
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import linux_recovery
import server
import update_manager


class RecoveryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux /proc')
    def test_linux_stop_finds_owned_backend_without_pid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app').mkdir()
            script = root / 'app/server.py'
            script.write_text('import time\ntime.sleep(120)\n')
            child = subprocess.Popen([sys.executable, str(script)])
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
            try:
                with patch.object(linux_recovery, 'ROOT', root), patch.dict(os.environ, {'XDG_CACHE_HOME': str(root / 'cache')}):
                    linux_recovery.stop()
                self.assertIsNotNone(child.poll())
                self.assertIsNone(unrelated.poll())
            finally:
                for process in (child, unrelated):
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)

    def test_legacy_updater_delivers_recovery_and_reports_through_static_config(self):
        # Execute the original Linux updater, not today's fixed updater.
        old = subprocess.check_output(['git', 'show', '3fe8476:app/update_manager.py'], cwd=ROOT).decode()
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory)
            (install / 'app').mkdir()
            (install / 'ui').mkdir()
            (install / 'version.json').write_text('{"version":"0.3.0"}')
            (install / 'ui/config.json').write_text('{}')
            namespace = {'__file__': str(install / 'app/update_manager.py')}
            exec(compile(old, 'legacy_updater', 'exec'), namespace)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                for name in ['version.json', 'start_offlineai.sh', 'stop_offlineai.sh',
                             'app/linux_recovery.py', 'app/server.py', 'app/rag_backend.py',
                             'ui/app.js', 'ui/config.json']:
                    archive.writestr('package/' + name, (ROOT / name).read_bytes())
            namespace['check_updates'] = lambda: dict(ok=True, updateAvailable=True, repository='test/test', branch='main')
            namespace['_fetch'] = lambda *args, **kwargs: stream.getvalue()
            self.assertTrue(namespace['apply_update']()['applied'])
            self.assertIn('linux_recovery.py', (install / 'start_offlineai.sh').read_text())
            with patch.object(linux_recovery, 'ROOT', install):
                linux_recovery.report('failed', 'preflight: example error')
            self.assertEqual(json.loads((install / 'ui/config.json').read_text())['restartDiagnostic']['stage'], 'failed')

    def test_process_path_match_is_exact_and_ignores_unrelated_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            entry = proc / '987654'
            entry.mkdir()
            target = (proc / 'app/server.py').resolve()
            with patch.object(linux_recovery.os, 'getuid', return_value=entry.stat().st_uid, create=True):
                (entry / 'cmdline').write_bytes(b'python3\0' + str(target).encode() + b'\0')
                self.assertTrue(linux_recovery.owned_process(987654, {target}, proc))
                (entry / 'cmdline').write_bytes(b'python3\0' + str(target).encode() + b'.unrelated\0')
                self.assertFalse(linux_recovery.owned_process(987654, {target}, proc))

    def test_catalog_path_case_drift_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual_dir = root / 'worker-pdf'
            actual_dir.mkdir()
            actual_db = actual_dir / 'pdf_catalog.sqlite3'
            actual_db.touch()
            configured = root / 'worker-PDF' / 'PDF_catalog.sqlite3'
            resolved = server.SearchIndex._resolve_catalog_path(configured)
            self.assertEqual(resolved, actual_db)

    def test_chat_and_debug_over_http(self):
        class Runner(BaseHTTPRequestHandler):
            def do_GET(self):
                self.reply({'data': [{'id': 'qwen/qwen3-0.6b'}]})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert body['model'] == 'qwen/qwen3-0.6b'
                self.reply({'choices': [{'message': {'content': 'OK'}}]})
            def reply(self, data):
                raw = json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args):
                pass
        runner = ThreadingHTTPServer(('127.0.0.1', 0), Runner)
        backend = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        for service in (runner, backend):
            threading.Thread(target=service.serve_forever, daemon=True).start()
        try:
            with patch.object(server, 'lm', server.LMStudioClient(f'http://127.0.0.1:{runner.server_port}/v1')), patch.object(server, 'runtime_status', return_value={'running': True, 'model': 'qwen/qwen3-0.6b'}):
                base = f'http://127.0.0.1:{backend.server_port}'
                def request(route, data=None):
                    req = urllib.request.Request(base + route, data=json.dumps(data).encode() if data else None)
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.load(response)
                self.assertEqual(request('/api/health')['backend_build'], 'runner-diagnostics-v2')
                self.assertEqual(request('/api/config')['models'][0]['id'], 'qwen/qwen3-0.6b')
                self.assertTrue(request('/api/debug')['ok'])
                self.assertEqual(request('/api/chat', {'message': 'Hi', 'searchLibrary': False})['answer'], 'OK')
        finally:
            for service in (backend, runner):
                service.shutdown()
                service.server_close()


if __name__ == '__main__':
    unittest.main()
