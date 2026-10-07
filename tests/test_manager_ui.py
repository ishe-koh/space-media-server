import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import HTTPServer
from urllib.request import urlopen, Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlencode
from unittest.mock import patch

from app import web_ui
from app.web.dashboard import fingerprints, publication, record_publish, save_schedule
from app.limited_media import save_media_rule


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class ManagerUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "vision"
        for group in ("always", "wed", "is_limited"):
            (self.base / "source/media" / group).mkdir(parents=True)
        self.config = {"cabinet": {"width": 128, "height": 256}, "screen": {"cols": 1, "rows": 1},
                       "lanes": {"cols": 2, "rows": 1}, "lane_policy": {}, "encoding": {}}
        (self.base / "config").mkdir()
        (self.base / "config/vision_config.json").write_text(json.dumps(self.config))
        (self.base / "source/media/always/a.mp4").touch()
        (self.base / "source/media/always/b.png").touch()
        (self.base / "source/media/is_limited/c.mp4").touch()
        save_media_rule(self.base / "source", "media/is_limited/c.mp4", "", "2030-10-31T23:59:59+09:00")
        (self.base / "source/playlists").mkdir()
        (self.base / "source/playlists/always.json").write_text(json.dumps({"meta": {"default_volume": 20}, "lanes": {"lane0": {}, "lane1": {"volume": 30}}, "auto_policy": {"directory": "media/always", "mode": "replace_if_empty"}}))
        self.patchers = [patch.object(web_ui, "VISION_ROOT", self.root), patch.object(web_ui, "JOBS_DIR", self.root / "jobs")]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        self.server = HTTPServer(("127.0.0.1", 0), web_ui.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def post(self, path, data):
        try:
            response = build_opener(NoRedirect).open(Request(self.url + path, data=urlencode(data).encode()))
            return response.status, response.read().decode()
        except HTTPError as e:
            return e.code, e.read().decode()

    def upload(self, group, end="", name="new.mp4", overwrite=False):
        values = {"vision_id": "vision", "ui": "1", "purpose": group,
                  "upload_weekday": "wed", "available_until": end, "overwrite": "1" if overwrite else ""}
        boundary = "manager-test"
        parts = []
        for key, value in values.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: video/mp4\r\n\r\n'.encode() + b'video\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        request = Request(self.url + "/upload", data=b''.join(parts), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            response = build_opener(NoRedirect).open(request)
            return response.status
        except HTTPError as error:
            return error.code

    def test_all_tabs_render_without_internal_form_fields(self):
        for tab in ("media", "schedule", "publish", "guide", "settings"):
            page = urlopen(self.url + f"/?vision_id=vision&tab={tab}").read().decode()
            self.assertTrue('<html lang="ja">' in page, tab)
            self.assertFalse('name="auto_dir"' in page, tab)
            self.assertFalse('name="lane0_item1"' in page, tab)

    def test_upload_purposes_and_local_dates(self):
        self.assertEqual(self.upload("always"), 303)
        self.assertTrue((self.base / "source/media/always/new.mp4").exists())
        self.assertEqual(self.upload("weekday", name="wed.mp4"), 303)
        self.assertTrue((self.base / "source/media/wed/wed.mp4").exists())
        self.assertEqual(self.upload("is_limited", "2030-10-31T23:59:59", "limited.mp4"), 303)
        data = json.loads((self.base / "source/media_availability.json").read_text())
        self.assertEqual(data["media/is_limited/limited.mp4"]["is_available_until"], "2030-10-31T23:59:59+09:00")

    def test_invalid_and_duplicate_uploads_do_not_overwrite(self):
        self.assertEqual(self.upload("is_limited", name="missing.mp4"), 400)
        self.assertFalse((self.base / "source/media/is_limited/missing.mp4").exists())
        self.assertEqual(self.upload("always", name="a.mp4"), 400)
        self.assertEqual((self.base / "source/media/always/a.mp4").read_bytes(), b"")
        self.assertEqual(self.upload("always", name="a.mp4", overwrite=True), 303)

    def test_schedule_order_preserves_other_lane_and_metadata(self):
        status, _ = self.post("/save_schedule", {"vision_id": "vision", "ui": "1", "weekday": "always", "lane": "lane0",
            "selection_mode": "custom", "ordered_items": json.dumps(["media/always/b.png", "media/always/a.mp4"]),
            "volume": "45", "loop": "1", "all_day": "1", "weekday_limited_mode": "weekday_only"})
        self.assertEqual(status, 303)
        data = json.loads((self.base / "source/playlists/always.json").read_text())
        self.assertEqual(data["lanes"]["lane0"]["items"], ["media/always/b.png", "media/always/a.mp4"])
        self.assertEqual(data["lanes"]["lane1"], {"volume": 30})
        self.assertEqual(data["meta"], {"default_volume": 20})

    def test_invalid_schedule_does_not_change_playlist(self):
        before = (self.base / "source/playlists/always.json").read_bytes()
        status, _ = self.post("/save_schedule", {"vision_id": "vision", "weekday": "always", "lane": "lane0", "selection_mode": "custom", "ordered_items": '[]'})
        self.assertEqual(status, 400)
        self.assertEqual((self.base / "source/playlists/always.json").read_bytes(), before)

    def test_dates_and_content_have_separate_publication_states(self):
        snapshot = fingerprints(self.base)
        record_publish(self.base, "all", snapshot, "player")
        self.assertEqual(publication(self.base)[0], "synced")
        save_media_rule(self.base / "source", "media/always/a.mp4", "", "2030-12-31T23:59:59+09:00")
        self.assertIn("使用期限", publication(self.base)[1])
        self.assertEqual(fingerprints(self.base)["content"], snapshot["content"])
        (self.base / "source/media/always/a.mp4").write_bytes(b"changed")
        self.assertIn("素材・再生設定", publication(self.base)[1])

    def test_connection_is_saved_without_starting_deployment(self):
        status, _ = self.post("/save_connection", {"vision_id": "vision", "ui": "1", "target": "player.local", "user": "ishii"})
        self.assertEqual(status, 303)
        self.assertEqual(json.loads((self.base / "state/player_connection.json").read_text())["user"], "ishii")
        self.assertFalse((self.root / "jobs").exists())

    def test_missing_weekday_preview_uses_normal_content(self):
        page = urlopen(self.url + '/?vision_id=vision&tab=schedule&weekday=wed').read().decode()
        self.assertTrue('プレイリストは未作成' in page)
        self.assertTrue('a.mp4' in page)

    def test_date_only_publish_rejects_unpublished_content(self):
        from app.web.dashboard import save_connection
        save_connection(self.base, "demo-player", "ishii")
        (self.base / "output").mkdir()
        (self.base / "output/media_availability.json").write_text("{}")
        record_publish(self.base, "all", fingerprints(self.base), "demo-player")
        (self.base / "source/media/always/new.mp4").touch()
        with patch.object(web_ui, "_start_job") as start:
            status, body = self.post("/push_availability", {"ui": "1", "vision_id": "vision", "target_manual": "demo-player", "player_user": "ishii"})
        self.assertEqual(status, 400)
        self.assertIn("未反映", body)
        start.assert_not_called()
