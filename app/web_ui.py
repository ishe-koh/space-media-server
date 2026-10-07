#!/usr/bin/env python3
import html
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VISION_ROOT = REPO_ROOT / "vision_players"
LEASES_FILE = Path(os.environ.get("LEASES_FILE", "/var/lib/misc/dnsmasq.leases"))
KNOWN_TARGETS_FILE = REPO_ROOT / "config" / "known_targets.json"
JOBS_DIR = REPO_ROOT / "state" / "web_ui_jobs"

from app.web.known_targets import (
    is_ip,
    list_leases_hosts,
    load_known_targets,
    save_known_targets,
)
from app.web.media import (
    list_media_dirs,
    list_output_media,
    parse_multipart,
    read_playlist,
    save_upload,
)
from app.encoding_pipeline import expand_active_time_always
from app.limited_media import load_media_rules, save_media_rule, validate_window
from app.web.dashboard import (render_dashboard, render_job, save_schedule, save_connection,
                               iso_input, fingerprints, record_publish, DAYS, EXTENSIONS)


def _list_vision_ids() -> list[str]:
    if not VISION_ROOT.exists():
        return []
    return sorted([p.name for p in VISION_ROOT.iterdir() if p.is_dir()])


def _write_playlist(vision_id: str, weekday: str, payload: dict) -> Path:
    playlists_dir = VISION_ROOT / vision_id / "source" / "playlists"
    playlists_dir.mkdir(parents=True, exist_ok=True)
    out_path = playlists_dir / f"{weekday}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def _parse_bool(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "y", "on")


def _ensure_jobs_dir() -> Path:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return JOBS_DIR


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _job_paths(job_id: str) -> dict[str, Path]:
    base = _ensure_jobs_dir() / job_id
    return {
        "base": base,
        "stdout": base / "stdout.log",
        "stderr": base / "stderr.log",
        "meta": base / "meta.json",
    }


def _start_job(command: list[str], env: dict[str, str], cwd: Path, meta: dict) -> str:
    job_id = uuid.uuid4().hex
    paths = _job_paths(job_id)
    paths["base"].mkdir(parents=True, exist_ok=True)

    out_f = paths["stdout"].open("w", encoding="utf-8")
    err_f = paths["stderr"].open("w", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        env={**env, "PYTHONUNBUFFERED": "1"},
        stdin=subprocess.DEVNULL,
        stdout=out_f,
        stderr=err_f,
        text=True,
    )

    meta = {
        **meta,
        "job_id": job_id,
        "pid": proc.pid,
        "start_time": time.time(),
        "status": "running",
    }
    _write_json(paths["meta"], meta)

    def _wait_and_record() -> None:
        rc = proc.wait()
        out_f.close()
        err_f.close()
        meta["returncode"] = rc
        meta["end_time"] = time.time()
        meta["status"] = "ok" if rc == 0 else "err"
        if rc == 0 and meta.get("publish_snapshot"):
            record_publish(VISION_ROOT / meta["vision_id"], meta["weekday"],
                           meta["publish_snapshot"], meta.get("target", ""))
        _write_json(paths["meta"], meta)

    threading.Thread(target=_wait_and_record, daemon=True).start()
    return job_id


def _tail_text(path: Path, max_bytes: int = 40000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="ignore")


def _job_running(meta: dict) -> bool:
    pid = meta.get("pid")
    if not isinstance(pid, int):
        return False
    return Path(f"/proc/{pid}").exists()


class Handler(BaseHTTPRequestHandler):
    def _html(self, body: str, status: int = 200, refresh_sec: int | None = None) -> None:
        refresh_tag = f'<meta http-equiv="refresh" content="{refresh_sec}">' if refresh_sec else ""
        static = REPO_ROOT / "app/web/static"
        style = (static / "manager.css").read_text(encoding="utf-8")
        script = (static / "manager.js").read_text(encoding="utf-8")
        if 'class="app"' not in body and 'class="standalone"' not in body:
            body = '<main class="standalone"><section class="card">' + body + '</section></main>'
        content = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_tag}<title>entas | Vision Manager</title><style>{style}</style></head>
<body>{body}<script>{script}</script></body></html>"""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/job":
            self.do_GET_job(parse_qs(parsed.query))
            return
        if parsed.path == "/delete_media":
            self.do_GET_delete(parse_qs(parsed.query))
            return
        if parsed.path == "/delete_media_dir":
            self.do_GET_delete_dir(parse_qs(parsed.query))
            return
        if parsed.path == "/view_playlist":
            self.do_GET_view_playlist(parse_qs(parsed.query))
            return
        if parsed.path == "/view_output":
            self.do_GET_view_output(parse_qs(parsed.query))
            return
        if parsed.path == "/ping_target":
            self.do_GET_ping_target(parse_qs(parsed.query))
            return
        if parsed.path == "/delete_target":
            self.do_GET_delete_target(parse_qs(parsed.query))
            return

        vision_ids = _list_vision_ids()
        query = parse_qs(parsed.query)
        selected = query.get("vision_id", [""])[0]
        if selected not in vision_ids:
            selected = vision_ids[0] if vision_ids else ""
        if not selected:
            self._html("<h1>ビジョンがまだ登録されていません</h1><p>最初にビジョンの設定を作成してください。</p>")
            return
        targets = load_known_targets(KNOWN_TARGETS_FILE)
        known = {target.get("target") for target in targets}
        targets += [{"name": host, "target": host} for host in list_leases_hosts(LEASES_FILE) if host not in known and host != "*"]
        recent = []
        if JOBS_DIR.exists():
            for path in sorted(JOBS_DIR.glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    meta = json.loads(path.read_text())
                    if meta.get("vision_id") == selected:
                        if meta.get("status") == "running" and not _job_running(meta):
                            meta["status"] = "err"
                        recent.append(meta)
                except (ValueError, OSError):
                    continue
                if len(recent) == 8:
                    break
        try:
            body = render_dashboard(VISION_ROOT, selected, query.get("tab", ["media"])[0],
                                    query, targets, recent, query.get("notice", [""])[0])
        except (ValueError, OSError) as error:
            self._html(f"<h1>設定を読み込めませんでした</h1><p>{html.escape(str(error))}</p><a href='/'>管理画面へ戻る</a>", status=400)
            return
        self._html(body)

    def do_GET_job(self, query: dict[str, list[str]]) -> None:
        job_id = query.get("job_id", [""])[0]
        if not job_id:
            self._html("<p class='err'>missing job_id</p>", status=400)
            return
        paths = _job_paths(job_id)
        if not paths["meta"].exists():
            self._html("<p class='err'>job not found</p>", status=404)
            return
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
        running = _job_running(meta)
        stdout = _tail_text(paths["stdout"])
        stderr = _tail_text(paths["stderr"])
        self._html(render_job(meta, stdout, stderr, running), refresh_sec=3 if running else None)

    def _redirect_ui(self, vision_id, tab, notice="", **kwargs):
        self.send_response(303)
        self.send_header("Location", "/?" + urlencode({"vision_id": vision_id, "tab": tab, "notice": notice, **kwargs}))
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        data = raw.decode("utf-8", errors="ignore")
        form = {k: v[0] for k, v in parse_qs(data).items()}

        if form.get("ui") == "1" and self.path != "/upload" and form.get("vision_id") not in _list_vision_ids():
            self._html("<h1>ビジョンが見つかりません</h1>", status=400)
            return

        if self.path in {"/save_schedule", "/save_connection"}:
            vision_id = form.get("vision_id", "")
            if vision_id not in _list_vision_ids():
                self._html("<h1>ビジョンが見つかりません</h1>", status=400)
                return
            try:
                if self.path == "/save_schedule":
                    save_schedule(VISION_ROOT / vision_id, form)
                    self._redirect_ui(vision_id, "schedule", "再生設定を保存しました。Playerへの反映はまだです。", weekday=form.get("weekday", "always"), lane=form.get("lane", "lane0"))
                else:
                    save_connection(VISION_ROOT / vision_id, form.get("target", "").strip(), form.get("user", "").strip())
                    self._redirect_ui(vision_id, "settings", "接続設定を保存しました。")
            except (ValueError, OSError, TypeError) as error:
                self._html(f"<h1>保存できませんでした</h1><p>{html.escape(str(error))}</p><a href='javascript:history.back()'>入力画面に戻る</a>", status=400)
            return

        if self.path in {"/encode_push", "/push_availability"}:
            vision_id = form.get("vision_id", "")
            weekday = form.get("weekday", "always")
            if self.path == "/push_availability":
                weekday = "availability"
            target_manual = form.get("target_manual", "").strip()
            target_select = form.get("target_select", "").strip()
            player_user = form.get("player_user", "pi").strip() or "pi"
            env = os.environ.copy()
            env["VISION_ID"] = vision_id
            target = target_select or target_manual
            if form.get("ui") == "1":
                try:
                    if not form.get("player_user", "").strip():
                        raise ValueError("SSHユーザー名を入力してください。")
                    save_connection(VISION_ROOT / vision_id, target, player_user)
                    if weekday == "availability" and not (VISION_ROOT / vision_id / "output/media_availability.json").exists():
                        raise ValueError("最初に「素材と再生設定を反映」を実行してください。")
                    if weekday == "availability":
                        state_path = VISION_ROOT / vision_id / "state/published.json"
                        state = json.loads(state_path.read_text()) if state_path.exists() else {}
                        if state.get("content") != fingerprints(VISION_ROOT / vision_id)["content"]:
                            raise ValueError("素材・再生設定に未反映の変更があります。「素材と再生設定を反映」を実行してください。")
                    for job_path in JOBS_DIR.glob("*/meta.json"):
                        previous = json.loads(job_path.read_text())
                        if previous.get("vision_id") == vision_id and previous.get("status") == "running" and _job_running(previous):
                            raise ValueError("このビジョンの反映は処理中です。完了してから再実行してください。")
                except (ValueError, OSError) as error:
                    self._html(f"<h1>反映を開始できませんでした</h1><p>{html.escape(str(error))}</p><a href='javascript:history.back()'>戻る</a>", status=400)
                    return
            env.pop("PLAYER_IP", None)
            env.pop("PLAYER_HOSTNAME", None)
            if target:
                if is_ip(target):
                    env["PLAYER_IP"] = target
                else:
                    env["PLAYER_HOSTNAME"] = target
            env["PLAYER_USER"] = player_user
            if weekday in {"all", "availability"}:
                env.pop("PLAYLIST", None)
            else:
                env["PLAYLIST"] = f"source/playlists/{weekday}.json"

            meta = {
                "vision_id": vision_id,
                "weekday": weekday,
                "target": target or "",
                "player_user": player_user,
            }
            if form.get("ui") == "1":
                meta["publish_snapshot"] = fingerprints(VISION_ROOT / vision_id)
            job_id = _start_job(
                [sys.executable, str(REPO_ROOT / "bin" / "push_availability.py")] if weekday == "availability" else [str(REPO_ROOT / "bin" / "encode_and_push.sh")],
                env=env,
                cwd=REPO_ROOT,
                meta=meta,
            )
            self.send_response(302)
            self.send_header("Location", f"/job?job_id={job_id}")
            self.end_headers()
            return

        if self.path == "/save_target":
            name = form.get("name", "").strip()
            target = form.get("target", "").strip()
            ip = form.get("ip", "").strip()
            if not name or not target:
                self._html("<p class='err'>name and target required</p>", status=400)
                return
            targets = load_known_targets(KNOWN_TARGETS_FILE)
            updated = False
            for item in targets:
                if item.get("name") == name:
                    item["target"] = target
                    item["ip"] = ip
                    updated = True
                    break
            if not updated:
                targets.append({"name": name, "target": target, "ip": ip})
            save_known_targets(KNOWN_TARGETS_FILE, targets)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if self.path == "/save_weekday_limited_mode":
            vision_id = form.get("vision_id", "")
            weekday = form.get("weekday", "")
            mode = form.get("weekday_limited_mode", "")
            if (vision_id not in _list_vision_ids() or weekday not in {"always", "mon", "tue", "wed", "thu", "fri", "sat", "sun"}
                    or mode not in {"weekday_only", "weekday_plus_limited"}):
                self._html("<p class='err'>invalid vision, weekday or mode</p>", status=400)
                return
            path = VISION_ROOT / vision_id / "source" / "playlists" / f"{weekday}.json"
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                "lanes": {"lane0": {}},
                "auto_policy": {"directory": f"media/{weekday}", "mode": "replace_if_empty"},
            }
            payload["weekday_limited_mode"] = mode
            _write_playlist(vision_id, weekday, payload)
            self._html(f"<p class='ok'>再生設定を保存しました。Encode + Pushで反映してください。</p><a href='/?vision_id={html.escape(vision_id)}&weekday={weekday}'>back</a>")
            return

        if self.path == "/save_limited":
            try:
                vision_id = form.get("vision_id", "")
                filename = Path(form.get("filename", "")).name
                if vision_id not in _list_vision_ids() or not filename:
                    raise ValueError("unknown vision or filename")
                source_root = VISION_ROOT / vision_id / "source"
                media_dir = form.get("media_dir", "is_limited")
                if media_dir not in {"always", "mon", "tue", "wed", "thu", "fri", "sat", "sun", "is_limited"}:
                    raise ValueError("invalid media directory")
                if not (source_root / "media" / media_dir / filename).is_file():
                    raise ValueError("素材がありません")
                save_media_rule(source_root, f"media/{media_dir}/{filename}", iso_input(form.get("available_from", "").strip()),
                            iso_input(form.get("available_until", "").strip()))
            except (ValueError, OSError) as e:
                self._html(f"<p class='err'>{html.escape(str(e))}</p>", status=400)
                return
            if form.get("ui") == "1":
                self._redirect_ui(vision_id, "media", "使用期限を保存しました。既存素材なら期限だけの反映で更新できます。")
                return
            self._html(f"<p class='ok'>期限を保存しました。使用期限だけPushで反映できます。新しい素材はEncode + Pushが必要です。</p><a href='/?vision_id={html.escape(vision_id)}'>back</a>")
            return

        if self.path == "/upload":
            fields, files = parse_multipart(self.headers, raw)
            vision_id = fields.get("vision_id", "")
            media_dir = fields.get("media_dir", fields.get("weekday", "always"))
            if fields.get("purpose") in {"always", "is_limited", "weekday"}:
                media_dir = fields.get("upload_weekday", "mon") if fields["purpose"] == "weekday" else fields["purpose"]
            fileinfo = files.get("file")
            if not vision_id or fileinfo is None:
                self._html("<p class='err'>missing vision_id or file</p>", status=400)
                return
            try:
                filename, content = fileinfo
                if vision_id not in _list_vision_ids() or media_dir not in {"always", "mon", "tue", "wed", "thu", "fri", "sat", "sun", "is_limited"}:
                    raise ValueError("unknown vision or media directory")
                available_from = iso_input(fields.get("available_from", "").strip())
                available_until = iso_input(fields.get("available_until", "").strip())
                if Path(filename).suffix.lower() not in EXTENSIONS:
                    raise ValueError("対応していないファイル形式です。動画または画像を選んでください。")
                if fields.get("ui") == "1" and (VISION_ROOT / vision_id / "source/media" / media_dir / Path(filename).name).exists() and fields.get("overwrite") != "1":
                    raise ValueError("同名の素材があります。置き換える場合はチェックを入れてください。")
                if available_from and available_until:
                    from datetime import datetime
                    if datetime.fromisoformat(available_from) > datetime.fromisoformat(available_until):
                        raise ValueError("終了日時は開始日時より後にしてください。")
                if media_dir == "is_limited":
                    validate_window(available_from, available_until)
                dest = save_upload(VISION_ROOT, vision_id, media_dir, filename, content)
                if media_dir == "is_limited" or available_from or available_until:
                    save_media_rule(VISION_ROOT / vision_id / "source", f"media/{media_dir}/{dest.name}", available_from, available_until)
            except Exception as e:
                self._html(f"<h1>素材を保存できませんでした</h1><p>{html.escape(str(e))}</p><a href='javascript:history.back()'>入力画面に戻る</a>", status=400)
                return
            if fields.get("ui") == "1":
                self._redirect_ui(vision_id, "media", "素材を保存しました。Playerへ反映すると再生に使われます。")
                return
            body = f"""
<h1>upload done</h1>
<p class="ok">saved: {html.escape(str(dest))}</p>
<p><a href="/">back</a></p>
"""
            self._html(body)
            return

        if self.path == "/delete_media":
            self._html("<p class='err'>Use GET /delete_media</p>", status=405)
            return

        if self.path == "/delete_media_bulk":
            vision_id = form.get("vision_id", "")
            file_tokens = parse_qs(data).get("files", [])
            if not vision_id or not file_tokens:
                self._html("<p class='err'>no files selected</p>", status=400)
                return
            deleted = []
            for token in file_tokens:
                if "|" not in token:
                    continue
                weekday, name = token.split("|", 1)
                if weekday not in {*DAYS, "is_limited"}:
                    continue
                base_dir = VISION_ROOT / vision_id / "source" / "media" / weekday
                target = base_dir / Path(name).name
                if target.exists():
                    target.unlink()
                    deleted.append(f"{weekday}/{target.name}")
            if form.get("ui") == "1":
                self._redirect_ui(vision_id, "media", "素材を削除しました。Playerへ削除を反映するには全体の反映が必要です。")
                return
            body = f"""
<h1>delete done</h1>
<p class="ok">deleted: {html.escape(', '.join(deleted))}</p>
<p><a href="/?vision_id={html.escape(vision_id)}">back</a></p>
"""
            self._html(body)
            return

        if self.path == "/gen_playlist":
            vision_id = form.get("vision_id", "")
            weekday = form.get("weekday", "always")
            meta = {
                "default_volume": int(form.get("default_volume", "100")),
                "default_loop": _parse_bool(form.get("default_loop", "true")),
                "default_start_offset_sec": int(form.get("default_start_offset_sec", "0")),
            }
            active_from = form.get("active_from", "").strip()
            active_until = form.get("active_until", "").strip()
            auto_dir = form.get("auto_dir", "").strip()
            auto_mode = form.get("auto_mode", "replace_if_empty")
            auto_ext = form.get("auto_ext", "")
            extensions = [e.strip() for e in auto_ext.split(",") if e.strip()]
            auto_policy = {}
            if not auto_dir and auto_mode != "disabled":
                auto_dir = f"media/{weekday}"
            if auto_dir:
                auto_policy = {
                    "directory": auto_dir,
                    "sort": "asc",
                    "mode": auto_mode,
                    "extensions": extensions,
                }
            lane_count = int(form.get("lane_count", "1"))
            lanes = {}
            for i in range(lane_count):
                lane_id = f"lane{i}"
                items = []
                for j in range(1, 4):
                    key = f"{lane_id}_item{j}"
                    val = form.get(key, "").strip()
                    if not val:
                        continue
                    available_from = form.get(f"{key}_from", "").strip()
                    available_until = form.get(f"{key}_until", "").strip()
                    if available_from or available_until:
                        save_media_rule(VISION_ROOT / vision_id / "source", val, available_from, available_until)
                    items.append(val)
                lane_conf = {}
                if items:
                    lane_conf["items"] = items
                lanes[lane_id] = lane_conf

            playlist = {"meta": meta, "lanes": lanes}
            existing_path = VISION_ROOT / vision_id / "source" / "playlists" / f"{weekday}.json"
            if existing_path.exists():
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
                if "weekday_limited_mode" in existing:
                    playlist["weekday_limited_mode"] = existing["weekday_limited_mode"]
            if active_from or active_until:
                playlist["active_time"] = {
                    weekday: {
                        "from": active_from,
                        "until": active_until,
                    }
                }
            if auto_policy:
                playlist["auto_policy"] = auto_policy
            expand_active_time_always(playlist)

            out_path = _write_playlist(vision_id, weekday, playlist)
            body = f"""
<h1>playlist written</h1>
<p class="ok">path: {html.escape(str(out_path))}</p>
<pre class="mono">{html.escape(json.dumps(playlist, indent=2))}</pre>
<p><a href="/">back</a></p>
"""
            self._html(body)
            return

        if self.path == "/view_playlist":
            self._html("<p class='err'>Use GET /view_playlist</p>", status=405)
            return

        self._html("<p class='err'>Unknown route</p>", status=404)

    def do_DELETE(self) -> None:
        self._html("<p class='err'>Method not supported</p>", status=405)

    def do_GET_delete(self, query: dict) -> None:
        vision_id = query.get("vision_id", [""])[0]
        weekday = query.get("weekday", ["always"])[0]
        filename = query.get("filename", [""])[0]
        if not (vision_id and filename):
            self._html("<p class='err'>missing parameters</p>", status=400)
            return
        target = VISION_ROOT / vision_id / "source" / "media" / weekday / Path(filename).name
        if not target.exists():
            self._html("<p class='err'>file not found</p>", status=404)
            return
        target.unlink()
        self.send_response(302)
        self.send_header("Location", f"/?vision_id={vision_id}&weekday={weekday}")
        self.end_headers()
        return

    def do_GET_delete_dir(self, query: dict) -> None:
        vision_id = query.get("vision_id", [""])[0]
        weekday = query.get("weekday", ["always"])[0]
        if not vision_id:
            self._html("<p class='err'>missing vision_id</p>", status=400)
            return
        base_dir = VISION_ROOT / vision_id / "source" / "media" / weekday
        if not base_dir.exists():
            self._html("<p class='err'>directory not found</p>", status=404)
            return
        deleted = []
        for p in base_dir.iterdir():
            if p.is_file():
                p.unlink()
                deleted.append(p.name)
        self.send_response(302)
        self.send_header("Location", f"/?vision_id={vision_id}&weekday={weekday}")
        self.end_headers()
        return

    def do_GET_view_playlist(self, query: dict) -> None:
        vision_id = query.get("vision_id", [""])[0]
        weekday = query.get("weekday", ["always"])[0]
        if not vision_id:
            self._html("<p class='err'>missing vision_id</p>", status=400)
            return
        path, content = read_playlist(VISION_ROOT, vision_id, weekday)
        if not content:
            body = f"""
<h1>playlist</h1>
<p class="err">not found: {html.escape(str(path))}</p>
<p><a href="/">back</a></p>
"""
            self._html(body, status=404)
            return
        body = f"""
<h1>playlist</h1>
<p class="mono">{html.escape(str(path))}</p>
<pre class="mono">{html.escape(content)}</pre>
<p><a href="/">back</a></p>
"""
        self._html(body)
        return

    def do_GET_view_output(self, query: dict) -> None:
        vision_id = query.get("vision_id", [""])[0]
        weekday = query.get("weekday", ["always"])[0]
        if not vision_id:
            self._html("<p class='err'>missing vision_id</p>", status=400)
            return
        lanes = list_output_media(VISION_ROOT, vision_id, weekday)
        if not lanes:
            body = f"""
<h1>output order</h1>
<p class="err">not found: {html.escape(str(VISION_ROOT / vision_id / "output" / "media" / weekday))}</p>
<p><a href="/">back</a></p>
"""
            self._html(body, status=404)
            return
        sections = []
        for lane_id, files in lanes.items():
            items = "\n".join(f"<li>{html.escape(f.name)}</li>" for f in files) or "<li>(no files)</li>"
            sections.append(f"<h4>{html.escape(lane_id)}</h4><ol>{items}</ol>")
        body = f"""
<h1>output order</h1>
<p class="mono">{html.escape(str(VISION_ROOT / vision_id / "output" / "media" / weekday))}</p>
{''.join(sections)}
<p><a href="/">back</a></p>
"""
        self._html(body)
        return

    def do_GET_ping_target(self, query: dict) -> None:
        target = query.get("target", [""])[0]
        if not target:
            self._html("<p class='err'>missing target</p>", status=400)
            return
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", "1", target],
            capture_output=True,
            text=True,
        )
        status = "ok" if proc.returncode == 0 else "err"
        body = f"""
<h1>ping</h1>
<p class="{status}">target: {html.escape(target)}</p>
<pre class="mono">{html.escape(proc.stdout + proc.stderr)}</pre>
<p><a href="/">back</a></p>
"""
        self._html(body)

    def do_GET_delete_target(self, query: dict) -> None:
        name = query.get("name", [""])[0]
        if not name:
            self._html("<p class='err'>missing name</p>", status=400)
            return
        targets = load_known_targets(KNOWN_TARGETS_FILE)
        targets = [t for t in targets if t.get("name") != name]
        save_known_targets(KNOWN_TARGETS_FILE, targets)
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()


def main() -> None:
    host = os.environ.get("WEB_UI_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_UI_PORT", "8080"))
    server = HTTPServer((host, port), Handler)
    print(f"Web UI listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
