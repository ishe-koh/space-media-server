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
