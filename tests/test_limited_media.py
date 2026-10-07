import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.encoding_pipeline import build_encode_plan
from app.limited_media import save_window, validate_window


class LimitedMediaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        for directory in ("always", "wed", "is_limited"):
            (self.source / "media" / directory).mkdir(parents=True)
        (self.source / "media/always/001_regular.mp4").touch()
        (self.source / "media/is_limited/001_regular.mp4").touch()
        save_window(self.source, "001_regular.mp4", "2026-10-01T00:00:00+09:00", "2026-10-31T23:59:59+09:00")
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "cabinet": {"width": 128, "height": 256},
            "screen": {"cols": 1, "rows": 1},
            "lanes": {"cols": 1, "rows": 1},
            "lane_policy": {}, "encoding": {}}))

    def plan(self, weekday, mode="weekday_plus_limited"):
        path = self.root / f"{weekday}.json"
        path.write_text(json.dumps({"weekday_limited_mode": mode, "lanes": {"lane0": {}}, "auto_policy": {
            "directory": f"media/{weekday}", "mode": "replace_if_empty"}}))
        plan = build_encode_plan(path, self.config, self.source,
                                 self.root / "media", self.root / "playlists")
        plan.playlist_out.parent.mkdir(parents=True, exist_ok=True)
        plan.playlist_out.write_text(json.dumps(plan.playlist_json))
        for item in plan.items:
            item.output_path.parent.mkdir(parents=True, exist_ok=True)
            item.output_path.touch()
        return plan

    def player_items(self, plan, now):
        player_root = Path(__file__).resolve().parents[2] / "space-vision-player"
        if not player_root.is_dir():
            self.skipTest("sibling space-vision-player checkout required for integration test")
        code = """import json, sys
from pathlib import Path
from datetime import datetime
from app.playlist_loader import load_playlist
p = load_playlist(Path(sys.argv[1]), Path(sys.argv[2]), datetime.fromisoformat(sys.argv[3]))
print(json.dumps([str(i.path) for i in p['lanes']['lane0']['items']]))
"""
        result = subprocess.run([sys.executable, "-c", code, str(plan.playlist_out),
                                 str(self.root / "media"), now], cwd=player_root,
                                text=True, capture_output=True, check=True)
        return json.loads(result.stdout.splitlines()[-1])

    def test_empty_weekday_uses_always_and_shared_deadline(self):
        plan = self.plan("wed")
        before = self.player_items(plan, "2026-10-07T12:00:00+09:00")
        after = self.player_items(plan, "2026-11-01T00:00:00+09:00")
        self.assertEqual(len(before), 2)
        self.assertEqual(len(after), 1)
        self.assertFalse(any("_limited" in path for path in after))
        self.assertEqual(len({item.output_path for item in plan.items}), len(plan.items))

    def test_nonempty_weekday_uses_weekday_and_common_limited(self):
        (self.source / "media/wed/002_weekday.mp4").touch()
        plan = self.plan("wed")
        before = self.player_items(plan, "2026-10-07T12:00:00+09:00")
        self.assertEqual(len(before), 2)
        self.assertTrue(any("weekday" in path for path in before))
        self.assertFalse(any("_auto" in path and "regular" in path for path in before))

    def test_always_does_not_rediscover_expired_limited(self):
        plan = self.plan("always")
        self.assertEqual(len(self.player_items(plan, "2026-10-07T12:00:00+09:00")), 2)
        self.assertEqual(len(self.player_items(plan, "2026-11-01T00:00:00+09:00")), 1)
        self.assertEqual(len(self.player_items(plan, "2026-09-01T00:00:00+09:00")), 1)

    def test_unconfigured_limited_is_not_added(self):
        (self.source / "limited_media.json").unlink()
        plan = self.plan("wed")
        self.assertFalse(any(isinstance(item, dict) and "is_available_until" in item
                             for item in plan.playlist_json["lanes"]["lane0"]["items"]))

    def test_weekday_only_excludes_limited_even_before_deadline(self):
        (self.source / "media/wed/002_weekday.mp4").touch()
        plan = self.plan("wed", "weekday_only")
        items = self.player_items(plan, "2026-10-07T12:00:00+09:00")
        self.assertEqual(len(items), 1)
        self.assertIn("weekday", items[0])
        self.assertFalse(any("_limited" in str(item.output_path) for item in plan.items))

    def test_weekday_only_empty_folder_still_uses_always_and_limited(self):
        plan = self.plan("wed", "weekday_only")
        self.assertEqual(len(self.player_items(plan, "2026-10-07T12:00:00+09:00")), 2)
        self.assertEqual(len(self.player_items(plan, "2026-11-01T00:00:00+09:00")), 1)

    def test_always_includes_limited_regardless_of_weekday_mode(self):
        plan = self.plan("always", "weekday_only")
        self.assertEqual(len(self.player_items(plan, "2026-10-07T12:00:00+09:00")), 2)

    def test_window_validation(self):
        for start, end in [("", ""), ("", "2026-10-31T23:59:59"),
                           ("2026-11-01T00:00:00+09:00", "2026-10-31T23:59:59+09:00")]:
            with self.assertRaises(ValueError):
                validate_window(start, end)
