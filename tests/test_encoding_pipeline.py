import json
import tempfile
import unittest
from pathlib import Path

from app.encoding_pipeline import WEEKDAYS, build_encode_plan


class ActiveTimeExpansionTest(unittest.TestCase):
    def test_active_time_always_expands_to_weekdays_in_output_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(root)
            playlist_path = root / "always.json"
            playlist_path.write_text(
                json.dumps(
                    {
                        "meta": {},
                        "active_time": {
                            "always": {"from": "10:00", "until": "20:00"}
                        },
                        "lanes": {"lane0": {}},
                    }
                ),
                encoding="utf-8",
            )

            plan = build_encode_plan(
                playlist_path=playlist_path,
                config_path=config_path,
                source_root=root / "source",
                encoded_dir=root / "encoded",
                playlists_dir=root / "playlists",
            )

            self.assertEqual(plan.weekday, "always")
            self.assertNotIn("always", plan.playlist_json["active_time"])
            self.assertEqual(set(plan.playlist_json["active_time"].keys()), set(WEEKDAYS))
            for weekday in WEEKDAYS:
                self.assertEqual(
                    plan.playlist_json["active_time"][weekday],
                    {"from": "10:00", "until": "20:00"},
                )

    def test_active_time_weekday_values_override_always(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(root)
            playlist_path = root / "always.json"
            playlist_path.write_text(
                json.dumps(
                    {
                        "meta": {},
                        "active_time": {
                            "always": {"from": "10:00", "until": "20:00"},
                            "mon": {"from": "09:00", "until": "18:00"},
                        },
                        "lanes": {"lane0": {}},
                    }
                ),
                encoding="utf-8",
            )

            plan = build_encode_plan(
                playlist_path=playlist_path,
                config_path=config_path,
                source_root=root / "source",
                encoded_dir=root / "encoded",
                playlists_dir=root / "playlists",
            )

            self.assertEqual(
                plan.playlist_json["active_time"]["mon"],
                {"from": "09:00", "until": "18:00"},
            )
            self.assertEqual(
                plan.playlist_json["active_time"]["tue"],
                {"from": "10:00", "until": "20:00"},
            )
            self.assertNotIn("always", plan.playlist_json["active_time"])

    def test_auto_policy_directory_is_output_media_relative_per_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(root)
            (root / "source" / "media" / "always").mkdir(parents=True)
            playlist_path = root / "always.json"
            playlist_path.write_text(
                json.dumps(
                    {
                        "meta": {},
                        "auto_policy": {
                            "directory": "media/always",
                            "sort": "asc",
                            "mode": "replace_if_empty",
                        },
                        "lanes": {"lane0": {}},
                    }
                ),
                encoding="utf-8",
            )

            plan = build_encode_plan(
                playlist_path=playlist_path,
                config_path=config_path,
                source_root=root / "source",
                encoded_dir=root / "encoded",
                playlists_dir=root / "playlists",
            )

            self.assertNotIn("auto_policy", plan.playlist_json)
            self.assertEqual(
                plan.playlist_json["lanes"]["lane0"]["auto_policy"]["directory"],
                "always/lane0",
            )

    def _write_config(self, root: Path) -> Path:
        config_path = root / "vision_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "cabinet": {"width": 128, "height": 256},
                    "screen": {"cols": 1, "rows": 1},
                    "lanes": {"cols": 1, "rows": 1},
                    "lane_policy": {},
                    "encoding": {},
                }
            ),
            encoding="utf-8",
        )
        return config_path


if __name__ == "__main__":
    unittest.main()
