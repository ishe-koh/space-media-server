import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class PushAvailabilityTest(unittest.TestCase):
    def test_deadline_only_and_single_weekday_include_common_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bin").mkdir()
            shutil.copy(Path(__file__).resolve().parents[1] / "bin/push_media.sh", root / "bin/push_media.sh")
            output = root / "vision_players/test/output"
            (output / "media/always/shared/lane0").mkdir(parents=True)
            (output / "media/always/shared/lane0/common.mp4").touch()
            (output / "playlists").mkdir()
            (output / "playlists/wed.json").write_text("{}")
            (output / "media_availability.json").write_text("{}")
            (root / "leases").touch()
            fake = root / "fake"
            fake.mkdir()
            log = root / "calls"
            for tool in ("ssh", "rsync"):
                path = fake / tool
                path.write_text("#!/usr/bin/env python3\nimport os,sys,json\nwith open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(sys.argv) + '\\n')\n")
                path.chmod(0o755)
            env = {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"],
                "CALL_LOG": str(log), "VISION_ID": "test", "PLAYER_IP": "192.0.2.1",
                "PLAYER_USER": "test", "LEASES_FILE": str(root / "leases")}
            for availability_only in (True, False):
                log.write_text("")
                run_env = {**env, "PUSH_AVAILABILITY_ONLY": "1" if availability_only else "0", "PUSH_WEEKDAY": "wed"}
                result = subprocess.run(["bash", str(root / "bin/push_media.sh")], cwd=root,
                    env=run_env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                transfers = [call for call in calls if Path(call[0]).name == "rsync"]
                self.assertTrue(any("media_availability.json" in call[-2] for call in transfers))
                if availability_only:
                    self.assertEqual(len(transfers), 1)
                    self.assertIn("-azc", transfers[0])
                else:
                    self.assertEqual(len(transfers), 3)
                    self.assertTrue(any(call[-2] == str(output / "media") + "/" for call in transfers))
                    self.assertTrue(any("wed.json" in call[-2] for call in transfers))
