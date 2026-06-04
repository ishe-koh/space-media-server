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
from urllib.parse import parse_qs, urlparse


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
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
        env=env,
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
        content = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    {refresh_tag}
    <title>space-media-server</title>
    <style>
      body {{ font-family: sans-serif; margin: 16px; }}
      h1 {{ margin: 0 0 8px 0; }}
      details {{ border: 1px solid #ddd; border-radius: 6px; padding: 8px 12px; margin-bottom: 10px; }}
      summary {{ cursor: pointer; font-weight: 600; }}
      label {{ display: block; margin: 6px 0; }}
      input[type=text], select {{ width: 100%; max-width: 100%; }}
      .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
      .row {{ margin: 6px 0; }}
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
      .ok {{ color: #1a7f37; }}
      .err {{ color: #b42318; }}
      @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
    </style>
  </head>
  <body>
    {body}
  </body>
</html>"""
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
        lease_hosts = list_leases_hosts(LEASES_FILE)
        known_targets = load_known_targets(KNOWN_TARGETS_FILE)
        query = parse_qs(parsed.query)
        selected_vision = query.get("vision_id", [""])[0]
        selected_weekday = query.get("weekday", ["always"])[0]
        if not selected_vision and vision_ids:
            selected_vision = vision_ids[0]

        vision_opts = "\n".join(
            f"<option value='{html.escape(v)}' {'selected' if v == selected_vision else ''}>{html.escape(v)}</option>"
            for v in vision_ids
        )
        target_opts = "\n".join(
            f"<option value='{html.escape(t.get('target',''))}'>{html.escape(t.get('name',''))}</option>"
            for t in known_targets
        ) + "\n" + "\n".join(
            f"<option value='{html.escape(h)}'>{html.escape(h)}</option>"
            for h in lease_hosts
        )
        known_list = "\n".join(
            f"<li>{html.escape(t.get('name',''))} "
            f"({html.escape(t.get('target',''))}"
            f"{' / ' + html.escape(t.get('ip','')) if t.get('ip') else ''}) "
            f"<a href='/ping_target?target={html.escape(t.get('ip') or t.get('target',''))}'>ping</a> "
            f"<a href='/delete_target?name={html.escape(t.get('name',''))}'>delete</a>"
            f"</li>"
            for t in known_targets
        ) or "<li class='mono'>(no known targets)</li>"
        weekdays = ["always", "mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        upload_dir_choices = [(w, w) for w in weekdays] + [("is_limited", "limited")]
        weekday_opts = "\n".join(
            f"<option value='{w}' {'selected' if w == selected_weekday else ''}>{w}</option>"
            for w in weekdays
        )
        media_dirs = list_media_dirs(VISION_ROOT, selected_vision) if selected_vision else {}
        selected_upload_dir = query.get("media_dir", [selected_weekday])[0]
        valid_upload_values = [value for value, _label in upload_dir_choices]
        if selected_upload_dir not in valid_upload_values:
            selected_upload_dir = "always"
        upload_dir_opts = "\n".join(
            f"<option value='{html.escape(value)}' {'selected' if value == selected_upload_dir else ''}>{html.escape(label)}</option>"
            for value, label in upload_dir_choices
        )
        media_list_sections = []
        for weekday, files in media_dirs.items():
            delete_dir_url = (
                f"/delete_media_dir?vision_id={html.escape(selected_vision)}"
                f"&weekday={html.escape(weekday)}"
            )
            file_items = "\n".join(
                f"<li>"
                f"<label>"
                f"<input type='checkbox' name='files' data-weekday='{html.escape(weekday)}' "
                f"value='{html.escape(weekday)}|{html.escape(p.name)}'> "
                f"{html.escape(p.name)}"
                f"</label> "
                f"<a href='/delete_media?vision_id={html.escape(selected_vision)}&weekday={html.escape(weekday)}&filename={html.escape(p.name)}' "
                f"onclick=\"return confirm('Delete {html.escape(p.name)}?');\">delete</a>"
                f"</li>"
                for p in files
            ) or "<li class='mono'>(no files)</li>"
            media_list_sections.append(
                f"<h4>{html.escape(weekday)}</h4>"
                f"<label><input type='checkbox' data-select-all='{html.escape(weekday)}'> select all</label>"
                f" <a href='{delete_dir_url}' "
                f"onclick=\"return confirm('Delete all in {html.escape(weekday)}?');\">delete all</a>"
                f"<ul>{file_items}</ul>"
            )
        media_list_html = "\n".join(media_list_sections) or "<p class='mono'>(no media dirs)</p>"
        body = f"""
<h1>space-media-server</h1>
<p class="mono">REPO_ROOT: {html.escape(str(REPO_ROOT))}</p>

<details open>
  <summary>Encode + Push</summary>
  <form method="POST" action="/encode_push">
    <div class="grid">
      <label>VISION_ID
        <select name="vision_id">{vision_opts}</select>
      </label>
      <label>Weekday
        <select name="weekday">
          <option value="all">all</option>
          {weekday_opts}
        </select>
      </label>
      <label>Target (hostname or IP)
        <input type="text" name="target_manual" placeholder="vision-player-akiba-01 or 192.168.x.x">
      </label>
      <label>Target (known / leases)
        <select name="target_select">
          <option value="">(select)</option>
          {target_opts}
        </select>
      </label>
      <label>USER (optional)
        <input type="text" name="player_user" placeholder="pi">
      </label>
    </div>
    <button type="submit">Run encode_and_push</button>
  </form>
</details>

<details>
  <summary>Known targets</summary>
  <form method="POST" action="/save_target">
    <div class="grid">
      <label>Name
        <input type="text" name="name" placeholder="vision-player-akiba-01">
      </label>
      <label>Target (hostname or IP)
        <input type="text" name="target" placeholder="vision-player-akiba-01 or 192.168.x.x">
      </label>
      <label>IP (optional)
        <input type="text" name="ip" placeholder="192.168.x.x">
      </label>
    </div>
    <button type="submit">Save target</button>
  </form>
  <ul>
    {known_list}
  </ul>
</details>

<details open>
  <summary>Upload Media</summary>
  <form method="POST" action="/upload" enctype="multipart/form-data">
    <div class="grid">
      <label>VISION_ID
        <select name="vision_id">{vision_opts}</select>
      </label>
      <label>Media Directory
        <select name="media_dir">
          {upload_dir_opts}
        </select>
      </label>
      <label>File
        <input type="file" name="file">
      </label>
    </div>
    <button type="submit">Upload</button>
  </form>
  <hr>
  <form method="GET" action="/">
    <input type="hidden" name="vision_id" value="{html.escape(selected_vision)}">
    <input type="hidden" name="weekday" value="{html.escape(selected_weekday)}">
    <button type="submit">Refresh file list</button>
  </form>
  <form method="POST" action="/delete_media_bulk" onsubmit="return confirm('Delete selected files?');">
    <input type="hidden" name="vision_id" value="{html.escape(selected_vision)}">
    <div class="row">
      <button type="button" id="select-all">Select all</button>
      <button type="button" id="clear-all">Clear all</button>
    </div>
    {media_list_html}
    <button type="submit">Delete selected</button>
  </form>
</details>
<script>
  document.querySelectorAll('input[data-select-all]').forEach(function(cb) {{
    cb.addEventListener('change', function() {{
      var weekday = cb.getAttribute('data-select-all');
      document.querySelectorAll('input[data-weekday=\"' + weekday + '\"]').forEach(function(x) {{
        x.checked = cb.checked;
      }});
    }});
  }});
  document.getElementById('select-all').addEventListener('click', function() {{
    document.querySelectorAll('input[name=\"files\"]').forEach(function(x) {{ x.checked = true; }});
  }});
  document.getElementById('clear-all').addEventListener('click', function() {{
    document.querySelectorAll('input[name=\"files\"]').forEach(function(x) {{ x.checked = false; }});
  }});
</script>

<details>
  <summary>Generate Playlist</summary>
  <form method="POST" action="/gen_playlist">
    <div class="grid">
      <label>VISION_ID
        <select name="vision_id">{vision_opts}</select>
      </label>
      <label>Weekday
        <select name="weekday">
          <option value="always">always</option>
          <option value="mon">mon</option>
          <option value="tue">tue</option>
          <option value="wed">wed</option>
          <option value="thu">thu</option>
          <option value="fri">fri</option>
          <option value="sat">sat</option>
          <option value="sun">sun</option>
        </select>
      </label>
      <label>default_volume
        <input type="text" name="default_volume" value="100">
      </label>
      <label>default_loop
        <select name="default_loop">
          <option value="true" selected>true</option>
          <option value="false">false</option>
        </select>
      </label>
      <label>default_start_offset_sec
        <input type="text" name="default_start_offset_sec" value="0">
      </label>
      <label>active_time.from (HH:MM)
        <input type="text" name="active_from" value="10:00" placeholder="10:00">
      </label>
      <label>active_time.until (HH:MM)
        <input type="text" name="active_until" value="20:00" placeholder="20:00">
      </label>
      <label>auto_policy.directory
        <input type="text" name="auto_dir" value="" placeholder="If empty, uses media/&lt;weekday&gt;">
      </label>
      <label>auto_policy.mode
        <select name="auto_mode">
          <option value="replace_if_empty" selected>replace_if_empty</option>
          <option value="append_remaining">append_remaining</option>
          <option value="disabled">disabled</option>
        </select>
      </label>
      <label>auto_policy.extensions (comma)
        <input type="text" name="auto_ext" value=".mp4,.mov,.m4v,.mkv,.webm,.avi,.mpg,.mpeg,.png,.jpg,.jpeg,.bmp,.webp">
      </label>
      <label>lane count
        <select name="lane_count">
          <option value="1" selected>1</option>
          <option value="2">2</option>
          <option value="3">3</option>
        </select>
      </label>
    </div>
    <p>Items (max 3 per lane). Empty source = skip.</p>
    <div class="row">
      <strong>lane0</strong><br>
      <input type="text" name="lane0_item1" placeholder="media/{html.escape(selected_weekday)}/foo.mp4">
      <input type="text" name="lane0_item1_from" placeholder="is_available_from (optional): 2026-03-10T00:00:00+09:00">
      <input type="text" name="lane0_item1_until" placeholder="is_available_until (optional): 2026-03-31T23:59:59+09:00">
      <input type="text" name="lane0_item2" placeholder="media/{html.escape(selected_weekday)}/bar.mp4">
      <input type="text" name="lane0_item2_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane0_item2_until" placeholder="is_available_until (optional)">
      <input type="text" name="lane0_item3" placeholder="media/{html.escape(selected_weekday)}/baz.mp4">
      <input type="text" name="lane0_item3_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane0_item3_until" placeholder="is_available_until (optional)">
    </div>
    <div class="row">
      <strong>lane1</strong><br>
      <input type="text" name="lane1_item1" placeholder="media/{html.escape(selected_weekday)}/foo.mp4">
      <input type="text" name="lane1_item1_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane1_item1_until" placeholder="is_available_until (optional)">
      <input type="text" name="lane1_item2" placeholder="media/{html.escape(selected_weekday)}/bar.mp4">
      <input type="text" name="lane1_item2_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane1_item2_until" placeholder="is_available_until (optional)">
      <input type="text" name="lane1_item3" placeholder="media/{html.escape(selected_weekday)}/baz.mp4">
      <input type="text" name="lane1_item3_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane1_item3_until" placeholder="is_available_until (optional)">
    </div>
    <div class="row">
      <strong>lane2</strong><br>
      <input type="text" name="lane2_item1" placeholder="media/{html.escape(selected_weekday)}/foo.mp4">
      <input type="text" name="lane2_item1_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane2_item1_until" placeholder="is_available_until (optional)">
      <input type="text" name="lane2_item2" placeholder="media/{html.escape(selected_weekday)}/bar.mp4">
      <input type="text" name="lane2_item2_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane2_item2_until" placeholder="is_available_until (optional)">
      <input type="text" name="lane2_item3" placeholder="media/{html.escape(selected_weekday)}/baz.mp4">
      <input type="text" name="lane2_item3_from" placeholder="is_available_from (optional)">
      <input type="text" name="lane2_item3_until" placeholder="is_available_until (optional)">
    </div>
    <button type="submit">Write playlist</button>
  </form>
  <hr>
  <form method="GET" action="/view_playlist">
    <label>VISION_ID
      <select name="vision_id">{vision_opts}</select>
    </label>
    <label>Weekday
      <select name="weekday">
        {weekday_opts}
      </select>
    </label>
    <button type="submit">View current playlist</button>
  </form>
  <form method="GET" action="/view_output">
    <label>VISION_ID
      <select name="vision_id">{vision_opts}</select>
    </label>
    <label>Weekday
      <select name="weekday">
        {weekday_opts}
      </select>
    </label>
    <button type="submit">View output order</button>
  </form>
</details>
"""
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
        status = "ok" if meta.get("status") == "ok" else "err"
        if running:
            status = "ok"
        body = f"""
<h1>encode_and_push</h1>
<p>Status: <span class="{status}">{html.escape(meta.get('status','running'))}</span></p>
<p>VISION_ID: {html.escape(str(meta.get('vision_id','')))}</p>
<p>Weekday: {html.escape(str(meta.get('weekday','')))}</p>
<pre class="mono">{html.escape(stdout)}</pre>
<pre class="mono">{html.escape(stderr)}</pre>
<p><a href="/">back</a></p>
"""
        refresh = 3 if running else None
        self._html(body, status=200, refresh_sec=refresh)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        data = raw.decode("utf-8", errors="ignore")
        form = {k: v[0] for k, v in parse_qs(data).items()}

        if self.path == "/encode_push":
            vision_id = form.get("vision_id", "")
            weekday = form.get("weekday", "always")
            target_manual = form.get("target_manual", "").strip()
            target_select = form.get("target_select", "").strip()
            player_user = form.get("player_user", "").strip()
            env = os.environ.copy()
            env["VISION_ID"] = vision_id
            target = target_select or target_manual
            if target:
                if is_ip(target):
                    env["PLAYER_IP"] = target
                else:
                    env["PLAYER_HOSTNAME"] = target
            if player_user:
                env["PLAYER_USER"] = player_user
            if weekday == "all":
                env.pop("PLAYLIST", None)
            else:
                env["PLAYLIST"] = f"source/playlists/{weekday}.json"

            meta = {
                "vision_id": vision_id,
                "weekday": weekday,
                "target": target or "",
                "player_user": player_user or "",
            }
            job_id = _start_job(
                [str(REPO_ROOT / "bin" / "encode_and_push.sh")],
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

        if self.path == "/upload":
            fields, files = parse_multipart(self.headers, raw)
            vision_id = fields.get("vision_id", "")
            media_dir = fields.get("media_dir", fields.get("weekday", "always"))
            fileinfo = files.get("file")
            if not vision_id or fileinfo is None:
                self._html("<p class='err'>missing vision_id or file</p>", status=400)
                return
            try:
                filename, content = fileinfo
                dest = save_upload(VISION_ROOT, vision_id, media_dir, filename, content)
            except Exception as e:
                self._html(f"<p class='err'>upload failed: {html.escape(str(e))}</p>", status=500)
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
                base_dir = VISION_ROOT / vision_id / "source" / "media" / weekday
                target = base_dir / Path(name).name
                if target.exists():
                    target.unlink()
                    deleted.append(f"{weekday}/{target.name}")
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
                        item = {"source": val}
                        if available_from:
                            item["is_available_from"] = available_from
                        if available_until:
                            item["is_available_until"] = available_until
                        items.append(item)
                    else:
                        items.append(val)
                lane_conf = {}
                if items:
                    lane_conf["items"] = items
                lanes[lane_id] = lane_conf

            playlist = {"meta": meta, "lanes": lanes}
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
