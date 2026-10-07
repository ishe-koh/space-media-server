import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import web_ui


class WebJobProgressTest(unittest.TestCase):
    def test_output_is_visible_before_process_finishes_and_stdin_is_closed(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(web_ui, "JOBS_DIR", Path(temp)):
            command = [sys.executable, "-c", "import sys,time; print('progress'); print('stdin=' + sys.stdin.read()); time.sleep(1)"]
            job = web_ui._start_job(command, os.environ.copy(), Path(temp), {})
            paths = web_ui._job_paths(job)
            try:
                deadline = time.monotonic() + 0.8
                while time.monotonic() < deadline and 'stdin=' not in paths['stdout'].read_text():
                    time.sleep(0.01)
                self.assertIn('progress', paths['stdout'].read_text())
                self.assertIn('stdin=', paths['stdout'].read_text())
                self.assertEqual(json.loads(paths['meta'].read_text())['status'], 'running')
            finally:
                deadline = time.monotonic() + 5
                while json.loads(paths['meta'].read_text())['status'] == 'running' and time.monotonic() < deadline:
                    time.sleep(0.02)
            self.assertEqual(json.loads(paths['meta'].read_text())['status'], 'ok')

    def test_all_invokes_explicit_noninteractive_encode_and_reaches_push(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'bin').mkdir()
            actual = Path(__file__).resolve().parents[1] / 'bin/encode_and_push.sh'
            shutil.copy(actual, root / 'bin/encode_and_push.sh')
            encoder = root / 'bin/encode.py'
            encoder.write_text("#!/bin/sh\n[ \"$3\" = --all ] || exit 21\n[ \"$PYTHONUNBUFFERED\" = 1 ] || exit 22\nprintf 'explicit all accepted\\n'\n")
            encoder.chmod(0o755)
            push = root / 'bin/push_media.sh'
            push.write_text("#!/bin/sh\nprintf 'push reached\\n'\n")
            push.chmod(0o755)
            env = {**os.environ, 'VISION_ID': 'test', 'CLEAN_OUTPUT': '0'}
            env.pop('PLAYLIST', None)
            result = subprocess.run(['bash', str(root / 'bin/encode_and_push.sh')], cwd=root,
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('explicit all accepted', result.stdout)
            self.assertIn('push reached', result.stdout)


class AvailabilityUITest(unittest.TestCase):
    def test_common_rule_save_and_date_only_push_command(self):
        import threading
        from http.server import HTTPServer
        from urllib.request import urlopen, Request
        from urllib.parse import urlencode
        with tempfile.TemporaryDirectory() as temp, patch.object(web_ui, "VISION_ROOT", Path(temp)):
            media = Path(temp) / "test/source/media/always/normal.mp4"
            media.parent.mkdir(parents=True)
            media.touch()
            server = HTTPServer(("127.0.0.1", 0), web_ui.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                page = urlopen(base + "/?vision_id=test").read().decode()
                self.assertIn("使用期限だけPush", page)
                self.assertIn("always/normal.mp4", page)
                fields = {"vision_id": "test", "media_dir": "always", "filename": "normal.mp4",
                          "available_until": "2026-10-31T23:59:59+09:00"}
                response = urlopen(Request(base + "/save_limited", data=urlencode(fields).encode()))
                self.assertEqual(response.status, 200)
                stored = json.loads((Path(temp) / "test/source/media_availability.json").read_text())
                self.assertEqual(stored["media/always/normal.mp4"]["is_available_until"], fields["available_until"])
                fields = {"vision_id": "test", "target_manual": "192.168.10.9", "player_user": "ishii"}
                with patch.object(web_ui, "_start_job", return_value="test-job") as start:
                    # Redirect handling would read a nonexistent job, so bypass it.
                    from urllib.request import build_opener, HTTPRedirectHandler
                    from urllib.error import HTTPError
                    class NoRedirect(HTTPRedirectHandler):
                        def redirect_request(self, *args):
                            return None
                    try:
                        build_opener(NoRedirect).open(Request(base + "/push_availability", data=urlencode(fields).encode()))
                    except HTTPError as error:
                        self.assertEqual(error.code, 302)
                    self.assertEqual(Path(start.call_args.args[0][-1]).name, "push_availability.py")
                    self.assertEqual(start.call_args.kwargs["env"]["PLAYER_USER"], "ishii")
                    self.assertNotIn("PLAYLIST", start.call_args.kwargs["env"])
            finally:
                server.shutdown()
                server.server_close()
