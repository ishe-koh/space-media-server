"""Human-oriented media management views and form operations."""
import hashlib
import html
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

from app.encoding_pipeline import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, _build_encode_items
from app.config_loader import load_vision_config
from app.limited_media import load_media_rules, save_media_rule
from app.web.media import list_media_dirs

JST = timezone(timedelta(hours=9))
DAYS = {"always": "通常", "mon": "月曜日", "tue": "火曜日", "wed": "水曜日", "thu": "木曜日", "fri": "金曜日", "sat": "土曜日", "sun": "日曜日"}
GROUPS = {**DAYS, "is_limited": "期間限定"}
EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
TABS = {"media": "素材", "schedule": "再生設定", "publish": "Playerへ反映", "guide": "使い方", "settings": "接続設定"}

def esc(value):
    return html.escape(str(value), quote=True)

def url(vision, tab="media", **kwargs):
    return "/?" + urlencode({"vision_id": vision, "tab": tab, **kwargs})

def hidden(vision):
    return f'<input type="hidden" name="vision_id" value="{esc(vision)}"><input type="hidden" name="ui" value="1">'

def local_input(value):
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone(JST).strftime("%Y-%m-%dT%H:%M:%S")

def iso_input(value):
    if not value:
        return ""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.isoformat()

def read_playlist(base, weekday):
    path = base / "source/playlists" / f"{weekday}.json"
    return json.loads(path.read_text()) if path.exists() else {}

def settings(base):
    path = base / "state/player_connection.json"
    return json.loads(path.read_text()) if path.exists() else {}

def fingerprints(base):
    content = hashlib.sha256()
    for path in sorted((base / "source").rglob("*")):
        if path.is_file() and ("media" in path.relative_to(base / "source").parts or path.parent.name == "playlists"):
            content.update(path.relative_to(base).as_posix().encode())
            if path.suffix == ".json":
                content.update(path.read_bytes())
            else:
                stat = path.stat()
                content.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode())
    for config in (base / "config/vision_config.json", base / "state/player_connection.json"):
        if config.exists():
            content.update(config.read_bytes())
    dates = hashlib.sha256(json.dumps(load_media_rules(base / "source"), sort_keys=True).encode()).hexdigest()
    return {"content": content.hexdigest(), "availability": dates}

def record_publish(base, mode, snapshot, target):
    path = base / "state/published.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    if mode == "all":
        state.update(snapshot)
    elif mode == "availability":
        state["availability"] = snapshot["availability"]
    else:
        # A weekday update does not claim that all other playlists have been published.
        state["partial_weekday"] = mode
    state.update({"at": datetime.now(JST).isoformat(), "target": target})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def publication(base):
    path = base / "state/published.json"
    if not path.exists():
        return "unknown", "反映の記録はまだありません", "最初は「素材と再生設定を反映」を実行してください。"
    state = json.loads(path.read_text())
    current = fingerprints(base)
    if current["content"] != state.get("content"):
        return "pending", "素材・再生設定に未反映の変更", "素材と再生設定をまとめて反映してください。"
    if current["availability"] != state.get("availability"):
        return "pending", "使用期限に未反映の変更", "期限だけの反映で更新できます。"
    return "synced", "最後の反映時点と一致", "Playerの現在の稼働状態を確認する表示ではありません。"

def save_connection(base, target, user):
    if not target.strip() or not user.strip():
        raise ValueError("Playerのホスト名またはIPと、SSHユーザー名を入力してください。")
    if target.startswith("-") or user.startswith("-") or any(c.isspace() for c in target + user):
        raise ValueError("接続先とユーザー名に空白は使用できません。")
    path = base / "state/player_connection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"target": target, "user": user}, ensure_ascii=False, indent=2))

def save_schedule(base, form):
    day = form.get("weekday", "always")
    if day not in DAYS:
        raise ValueError("曜日を選択してください。")
    config = load_vision_config(base / "config/vision_config.json")
    lane = form.get("lane", "lane0")
    if lane not in [f"lane{i}" for i in range(config.lanes.cols * config.lanes.rows)]:
        raise ValueError("表示エリアが不正です。")
    playlist = read_playlist(base, day)
    lane_conf = dict(playlist.get("lanes", {}).get(lane, {}))
    mode = form.get("selection_mode", "automatic")
    if mode not in {"automatic", "custom"}:
        raise ValueError("再生方法を選択してください。")
    if mode == "automatic":
        lane_conf.pop("items", None)
        lane_conf["auto_policy"] = {"directory": f"media/{day}", "mode": "replace_if_empty", "sort": "asc"}
    else:
        selected = json.loads(form.get("ordered_items", "[]"))
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
            raise ValueError("手動で並べる場合は素材を1つ以上選んでください。")
        existing = {}
        for item in lane_conf.get("items", []):
            key = item if isinstance(item, str) else item.get("source", item.get("path", ""))
            existing[key] = item
        for item in selected:
            path = Path(item)
            if not isinstance(item, str) or path.is_absolute() or ".." in path.parts or path.parts[:1] != ("media",) or not (base / "source" / path).is_file() or path.suffix.lower() not in EXTENSIONS:
                raise ValueError("選択した素材が見つかりません。素材一覧を更新してください。")
        lane_conf["items"] = [existing.get(item, item) for item in selected]
        lane_conf["auto_policy"] = {"mode": "disabled"}
    lane_conf["volume"] = int(form.get("volume", "100"))
    if not 0 <= lane_conf["volume"] <= 100:
        raise ValueError("音量は0〜100で指定してください。")
    lane_conf["loop"] = form.get("loop") == "1"
    playlist.setdefault("lanes", {})[lane] = lane_conf
    limited_mode = form.get("weekday_limited_mode", "weekday_plus_limited")
    if limited_mode not in {"weekday_only", "weekday_plus_limited"}:
        raise ValueError("期間限定素材の再生設定が不正です。")
    playlist["weekday_limited_mode"] = limited_mode
    if form.get("all_day") == "1":
        playlist.setdefault("active_time", {}).pop(day, None)
    else:
        start, end = form.get("active_from", ""), form.get("active_until", "")
        try:
            start_t = datetime.strptime(start, "%H:%M").time()
            end_t = datetime.strptime(end, "%H:%M").time()
        except ValueError:
            raise ValueError("再生開始・終了時刻を入力してください。")
        if start_t >= end_t:
            raise ValueError("終了時刻は開始時刻より後にしてください。日付をまたぐ時間帯には対応していません。")
        playlist.setdefault("active_time", {})[day] = {"from": start, "until": end}
    # always must expand when encoded, not here, to keep the normal editor understandable.
    if day == "always":
        for weekday in list(playlist.get("active_time", {})):
            if weekday != "always":
                playlist["active_time"].pop(weekday)
    target = base / "source/playlists" / f"{day}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(playlist, ensure_ascii=False, indent=2))

def asset_state(rule, limited=False):
    if limited and not rule.get("is_available_until"):
        return "missing", "期限を設定してください"
    now = datetime.now(JST)
    if rule.get("is_available_from") and now < datetime.fromisoformat(rule["is_available_from"]):
        return "waiting", "開始前"
    if rule.get("is_available_until") and now > datetime.fromisoformat(rule["is_available_until"]):
        return "expired", "期限終了"
    return "active", "期間内" if rule else "期限なし"

def date_fields(rule, required=False):
    return f'''<div class="two"><label>開始日時 <span>任意・日本時間</span><input type="datetime-local" step="1" name="available_from" value="{esc(local_input(rule.get('is_available_from', '')))}"></label><label>終了日時 <span>{'必須' if required else '任意'}・日本時間</span><input type="datetime-local" step="1" name="available_until" {'required' if required else ''} value="{esc(local_input(rule.get('is_available_until', '')))}"></label></div>'''

def render_media(vision, base, query):
    directories = list_media_dirs(base.parent, vision)
    rules = load_media_rules(base / "source")
    rows = []
    for group, files in directories.items():
        for media in files:
            if media.suffix.lower() not in EXTENSIONS:
                continue
            source = f"media/{group}/{media.name}"
            rule = rules.get(source, {})
            state, label = asset_state(rule, group == "is_limited")
            rows.append(f'''<article class="asset" data-source="{esc(source)}" data-search="{esc(media.name.lower())}"><div class="asset-icon">{'IMG' if media.suffix.lower() in IMAGE_EXTENSIONS else 'VID'}</div><div class="asset-main"><div class="asset-title">{esc(media.name)}</div><div class="asset-sub">{esc(GROUPS.get(group, group))} · {media.stat().st_size / 1024 / 1024:.1f} MB</div></div><span class="badge {state}">{label}</span><details class="asset-edit"><summary>期限を編集</summary><form method="POST" action="/save_limited">{hidden(vision)}<input type="hidden" name="filename" value="{esc(media.name)}"><input type="hidden" name="media_dir" value="{esc(group)}">{date_fields(rule, group == 'is_limited')}<button class="secondary">期限を保存</button></form></details><form method="POST" action="/delete_media_bulk" onsubmit="return confirm('この素材を削除しますか？ Playerへの削除反映には全体の反映が必要です。')">{hidden(vision)}<input type="hidden" name="files" value="{esc(group)}|{esc(media.name)}"><button class="text-danger">削除</button></form></article>''')
    return f'''<div class="page-heading"><div><p class="eyebrow">01 / MEDIA LIBRARY</p><h1>素材を用意する</h1><p>動画・画像を追加し、いつ流す素材かを決めます。</p></div><span class="count">{len(rows)} 素材</span></div>
<div class="split"><section class="card upload-card"><div class="section-title"><span class="step">1</span><h2>素材を追加</h2></div><form method="POST" action="/upload" enctype="multipart/form-data" id="upload-form">{hidden(vision)}
<label>この素材をいつ使いますか？</label><div class="purpose"><label><input type="radio" name="purpose" value="always" checked><strong>通常の素材</strong><span>曜日素材がない日に流す</span></label><label><input type="radio" name="purpose" value="weekday"><strong>特定の曜日</strong><span>毎週、その曜日に流す</span></label><label><input type="radio" name="purpose" value="is_limited"><strong>期間限定の素材</strong><span>キャンペーンや告知に</span></label></div>
<input type="hidden" name="media_dir" id="upload-directory" value="always"><label id="upload-day" hidden>使用する曜日<select id="upload-weekday" name="upload_weekday">{''.join(f'<option value="{day}">{label}</option>' for day,label in DAYS.items() if day != 'always')}</select></label>
<div class="dropzone"><span class="upload-glyph">↑</span><strong>動画・画像を選択</strong><p>MP4・MOVなどの動画、PNG・JPEGなどの画像</p><input type="file" name="file" accept="video/*,image/*" required id="upload-file"><span id="file-name">1ファイルずつ追加できます</span></div>
<div id="upload-dates"><p class="field-note" id="date-note">使用期間を付けたい場合だけ指定します。</p>{date_fields({})}</div><label class="check"><input type="checkbox" name="overwrite" value="1">同名の素材がある場合は置き換える</label><button class="primary wide">素材を保存</button><p class="hint">保存後、Playerへ反映すると再生に使われます。</p></form></section>
<section class="card library"><div class="section-title"><span class="step">2</span><h2>素材一覧</h2></div><p class="muted">期限は素材ごとの共通設定。各曜日へ書き直す必要はありません。</p><input type="search" id="asset-search" placeholder="素材名で検索" aria-label="素材を検索"><div class="asset-list">{''.join(rows) or '<div class="empty">素材はまだありません。左の「素材を追加」から始めてください。</div>'}</div><a class="inline-link" href="{url(vision, 'publish')}">保存した変更をPlayerへ反映 →</a></section></div>'''

def render_schedule(vision, base, query):
    day = query.get("weekday", ["always"])[0]
    if day not in DAYS:
        day = "always"
    config = load_vision_config(base / "config/vision_config.json")
    lane_ids = [f"lane{i}" for i in range(config.lanes.cols * config.lanes.rows)]
    lane = query.get("lane", ["lane0"])[0]
    if lane not in lane_ids:
        lane = lane_ids[0]
    playlist = read_playlist(base, day)
    lane_conf = playlist.get("lanes", {}).get(lane, {})
    selected = [item if isinstance(item, str) else item.get("source", item.get("path", "")) for item in lane_conf.get("items", [])]
    custom = bool(selected)
    candidates = []
    for group, files in list_media_dirs(base.parent, vision).items():
        if group == "is_limited":
            continue
        candidates.extend((f"media/{group}/{media.name}", media.name, GROUPS.get(group, group)) for media in files if media.suffix.lower() in EXTENSIONS)
    ordered = [item for item in selected if item in {source for source, _, _ in candidates}]
    candidates.sort(key=lambda item: (ordered.index(item[0]) if item[0] in ordered else len(ordered), item[0]))
    picker = ''.join(f'''<div class="order-row" data-source="{esc(source)}"><label><input type="checkbox" class="order-check" {'checked' if source in selected else ''}><span>{esc(name)}<small>{esc(group)}</small></span></label><div><button type="button" class="move-up" aria-label="上へ移動">↑</button><button type="button" class="move-down" aria-label="下へ移動">↓</button></div></div>''' for source, name, group in candidates)
    active = playlist.get("active_time", {}).get(day)
    if day == "always" and not active:
        values = list(playlist.get("active_time", {}).values())
        if values and all(value == values[0] for value in values):
            active = values[0]
    preview_items = []
    preview_note = "保存済みの設定から算出。まだPlayerへ反映していない変更も含みます。"
    preview_playlist = playlist or read_playlist(base, "always")
    if preview_playlist:
        plan_items, output = _build_encode_items(preview_playlist, base / "source", base / "output/media", day if playlist else "always", config)
        mapping = {item.output_path.relative_to(base / "output/media").as_posix(): item for item in plan_items}
        rules = load_media_rules(base / "source")
        for path in output.get("lanes", {}).get(lane, {}).get("items", []):
            item = mapping[path]
            source = item.source_path.relative_to(base / "source").as_posix()
            state, label = asset_state(rules.get(source, {}), source.startswith("media/is_limited/"))
            preview_items.append(f'<li><span>{esc(item.source_path.name)}</span><span class="badge {state}">{label}</span></li>')
    if not playlist:
        preview_note = "この曜日のプレイリストは未作成です。Playerは通常のプレイリストを使います。保存するとこの曜日の設定を作成します。"
    plus = playlist.get("weekday_limited_mode", "weekday_plus_limited") != "weekday_only"
    days = ''.join(f'<a class="{"selected" if item == day else ""}" href="{url(vision,"schedule",weekday=item,lane=lane)}">{label}</a>' for item,label in DAYS.items())
    return f'''<div class="page-heading"><div><p class="eyebrow">02 / PLAYBACK</p><h1>再生内容を決める</h1><p>曜日の切り替え、表示エリアごとの再生順、時間帯を設定します。</p></div></div><nav class="day-tabs">{days}</nav><div class="split"><section class="card"><form method="POST" action="/save_schedule" id="schedule-form">{hidden(vision)}<input type="hidden" name="weekday" value="{day}"><input type="hidden" name="lane" value="{lane}">
<div class="section-title"><h2>{DAYS[day]}の再生設定</h2></div><label>表示エリア<select onchange="location.href=this.value">{''.join(f'<option value="{url(vision,"schedule",weekday=day,lane=item)}" {"selected" if item == lane else ""}>表示エリア {i+1}</option>' for i,item in enumerate(lane_ids))}</select></label>
<fieldset><legend>素材の選び方</legend><label class="check"><input type="radio" name="selection_mode" value="automatic" {'checked' if not custom else ''}>用途に合わせて自動で選ぶ</label><p class="hint">{'通常の素材と期間限定素材を使います。' if day == 'always' else 'この曜日の素材を使い、曜日フォルダが空なら通常の素材に切り替えます。'}</p><label class="check"><input type="radio" name="selection_mode" value="custom" {'checked' if custom else ''}>素材を選んで順番を決める</label></fieldset>
<div id="custom-order" {'hidden' if not custom else ''}><p class="hint">チェックした素材を上から順に再生します。矢印で順序を変更できます。</p>{picker or '<p class="empty">通常・曜日素材を先にアップロードしてください。</p>'}<input type="hidden" name="ordered_items" id="ordered-items" value="{esc(json.dumps(ordered))}"></div>
<label>曜日素材がある日の期間限定素材<select name="weekday_limited_mode"><option value="weekday_only" {'selected' if not plus else ''}>曜日素材のみ</option><option value="weekday_plus_limited" {'selected' if plus else ''}>曜日素材＋期間限定素材</option></select></label><p class="hint">通常の日・曜日フォルダが空の日は、期限内の期間限定素材も使います。</p>
<fieldset><legend>再生する時間帯（全表示エリア共通）</legend><label class="check"><input type="checkbox" name="all_day" value="1" id="all-day" {'checked' if not active else ''}>終日再生</label><div class="two" id="time-range" {'hidden' if not active else ''}><label>開始<input type="time" name="active_from" value="{esc((active or {}).get('from','10:00'))}"></label><label>終了<input type="time" name="active_until" value="{esc((active or {}).get('until','20:00'))}"></label></div></fieldset><label>この表示エリアの音量<input type="number" name="volume" min="0" max="100" value="{esc(lane_conf.get('volume',playlist.get('meta',{}).get('default_volume',100)))}"></label><label class="check"><input type="checkbox" name="loop" value="1" {'checked' if lane_conf.get('loop',playlist.get('meta',{}).get('default_loop',True)) else ''}>繰り返し再生</label><button class="primary">再生設定を保存</button><p class="hint">保存後に「素材と再生設定を反映」を実行してください。</p></form></section><section class="card"><p class="eyebrow">SAVED PREVIEW</p><h2>保存済みの再生候補</h2><p class="muted">{preview_note}</p><ol class="preview-list">{''.join(preview_items) or '<li>再生候補はありません。</li>'}</ol><div class="notice">開始前・期限終了・期限未設定の期間限定素材は、Playerが再生から除外します。時間帯の制限もPlayerで判定します。</div><a class="inline-link" href="{url(vision,'publish')}">Playerへ反映 →</a></section></div>'''

def render_publish(vision, base, targets, recent_jobs):
    connection = settings(base)
    status, title, detail = publication(base)
    options = ''.join(f'<option value="{esc(t.get("ip") or t.get("target", ""))}">{esc(t.get("name",t.get("target","")))}</option>' for t in targets)
    jobs = ''.join(f'<a class="job-row" href="/job?job_id={esc(job["job_id"])}"><span>{"期限だけの反映" if job.get("weekday") == "availability" else "素材・再生設定の反映"}<small>{esc(job.get("target", ""))}</small></span><span class="badge {esc(job.get("status","running"))}">{ {"ok":"完了","err":"失敗","running":"処理中"}.get(job.get("status"),"未確認")}</span></a>' for job in recent_jobs)
    return f'''<div class="page-heading"><div><p class="eyebrow">03 / PUBLISH</p><h1>Playerへ反映する</h1><p>サーバーに保存した変更を、再生するPiへ送ります。</p></div></div><section class="status-card {status}"><span class="status-dot"></span><div><h2>{title}</h2><p>{detail}</p></div></section><div class="split"><section class="card"><h2>反映先のPlayer</h2><form method="POST" action="/encode_push" id="publish-form">{hidden(vision)}<input type="hidden" name="weekday" value="all"><label>ホスト名またはIP<input type="text" name="target_manual" value="{esc(connection.get('target',''))}" placeholder="vision-player-akiba-01" required></label><label>登録済みの接続先から選ぶ<select id="known-target"><option value="">選択すると上の欄に入力します</option>{options}</select></label><label>SSHユーザー名<input type="text" name="player_user" value="{esc(connection.get('user',''))}" placeholder="例：ishii" required></label><p class="hint">接続先はこのビジョンに保存され、次回も使えます。</p><div class="publish-choice"><h3>素材と再生設定を反映</h3><p>素材の追加・置換・削除、再生順や曜日設定を変えたとき。全曜日をまとめて更新します。</p><button class="primary wide">素材と再生設定を反映</button></div><div class="publish-choice"><h3>使用期限だけ反映</h3><p>すでに反映済みの素材の使用期限だけを変えたとき。動画は再エンコード・再転送しません。</p><button class="secondary wide" formaction="/push_availability">使用期限だけPush</button></div><p class="hint">転送中はPlayerに更新中の表示が出ます。電源を切らず、完了を確認してください。</p></form></section><section class="card"><h2>最近の反映</h2>{jobs or '<div class="empty">反映履歴はまだありません。</div>'}<div class="notice">保存しただけではPlayerは変わりません。素材はMediaServerで管理し、Playerは最後に受け取った内容を再生します。</div></section></div>'''

GUIDE = '''<div class="page-heading"><div><p class="eyebrow">OPERATING GUIDE</p><h1>素材の追加から再生まで</h1><p>期限と再生順を分けて管理するので、曜日ごとの期限入力は不要です。</p></div></div><div class="guide-grid"><section class="card"><span class="step">1</span><h2>素材の用途を選ぶ</h2><table><tr><th>通常の素材</th><td>曜日素材がない日に使う基本の動画・画像。</td></tr><tr><th>特定の曜日</th><td>毎週その曜日だけの動画・画像。</td></tr><tr><th>期間限定</th><td>キャンペーン・告知。終了日時を必ず設定。</td></tr></table><p>動画と画像をアップロードできます。画面サイズに合わせた変換はサーバーが行います。画像の表示秒数はビジョンの設定に従います。</p></section><section class="card"><span class="step">2</span><h2>再生設定を決める</h2><p>基本は「自動で選ぶ」で使えます。順番を指定したい場合は素材を選び、矢印で並べ替えます。表示エリアごとに順番と音量を設定できます。</p><p>曜日素材がある日は「曜日素材のみ」か「曜日素材＋期間限定」を選べます。自動選択で曜日素材が空の日は「通常＋期間限定」を使います。</p></section><section class="card"><span class="step">3</span><h2>Playerへ反映する</h2><p>素材・再生設定を変えたら全体を反映。使用期限だけ変えたら期限だけ反映。保存と反映は別の操作です。</p><p>共通素材は同じ設定なら一度の変換結果を全曜日で使います。転送中は更新中の表示になり、完了後にPlayerが再起動して新しい内容を使います。</p></section><section class="card"><h2>Playerが再生するとき</h2><ol><li>今日の曜日のプレイリストを選びます。曜日ファイルがなければ通常のプレイリストを使います。</li><li>再生順と自動選択を組み合わせます。</li><li>素材ごとの共通データを見て、開始前・期限終了の素材を除外します。</li><li>設定した時間帯だけ、残った素材を繰り返し再生します。</li></ol><p>期限・曜日の確認は約30秒間隔です。再生中の素材も期限切れを検知すると停止します。期限切れだけを理由に、曜日素材から通常素材へ切り替える仕様ではありません。</p></section><section class="card"><h2>よくある使い方</h2><p><strong>通常の広告：</strong>通常の素材へ追加 → 全体を反映。</p><p><strong>今月末までの告知：</strong>期間限定へ追加・終了日時を設定 → 全体を反映。</p><p><strong>告知を延長：</strong>素材一覧で終了日時を変更 → 使用期限だけ反映。</p><p><strong>水曜だけ別の広告：</strong>水曜日へ追加 → 水曜の再生設定を保存 → 全体を反映。</p></section><section class="card"><h2>電源と保存</h2><p>Overlayが有効な保存領域への更新は再起動で元に戻ります。通常保存するならOverlayを無効にし、正常なシャットダウンやUPSで電源断に備えてください。</p><p>素材・設定・期限はPiに保存されるため、反映後の再生はMediaServerへ常時接続する必要がありません。</p></section></div>'''

def render_dashboard(vision_root, vision, tab, query, targets, jobs, notice=""):
    base = vision_root / vision
    if tab not in TABS:
        tab = "media"
    if tab == "media":
        content = render_media(vision, base, query)
    elif tab == "schedule":
        content = render_schedule(vision, base, query)
    elif tab == "publish":
        content = render_publish(vision, base, targets, jobs)
    elif tab == "guide":
        content = GUIDE
    else:
        connection = settings(base)
        content = f'''<div class="page-heading"><div><p class="eyebrow">CONNECTION</p><h1>Playerの接続設定</h1><p>ビジョンと、素材を受け取るPiを対応付けます。</p></div></div><section class="card narrow"><form method="POST" action="/save_connection">{hidden(vision)}<label>ホスト名またはIP<input name="target" value="{esc(connection.get('target',''))}" required></label><label>SSHユーザー名<input name="user" value="{esc(connection.get('user',''))}" required></label><button class="primary">接続設定を保存</button></form><p class="hint">この設定は素材の反映先です。SSHの鍵やルーター設定は別途必要です。</p></section>'''
    ids = sorted(path.name for path in vision_root.iterdir() if path.is_dir())
    selector = ''.join(f'<option value="{url(item,tab)}" {"selected" if item == vision else ""}>{esc(item)}</option>' for item in ids)
    nav = ''.join(f'<a class="nav-item {"active" if key == tab else ""}" href="{url(vision,key)}"><span>{i+1:02d}</span>{title}</a>' for i,(key,title) in enumerate(TABS.items()))
    return f'''<div class="app"><aside><a class="brand" href="{url(vision)}"><span class="brand-mark">e</span><div>entas<span>VISION MANAGER</span></div></a><div class="sidebar-caption">WORKSPACE</div>{nav}<div class="sidebar-bottom">MediaServerで管理<br>Playerで再生</div></aside><div class="workspace"><header><span class="workspace-label">ビジョン</span><select aria-label="管理するビジョン" onchange="location.href=this.value">{selector}</select><a href="{url(vision,'guide')}">操作ガイド ↗</a></header><main>{f'<div class="toast" role="status">{esc(notice)}</div>' if notice else ''}{content}</main><footer>日時は日本時間。保存した変更はPlayerへの反映後に使われます。</footer></div></div>'''

def render_job(meta, stdout, stderr, running):
    success = meta.get("status") == "ok" and not running
    stage = "Playerへ反映しています" if running else "反映が完了しました" if success else "反映できませんでした"
    phase = "素材を変換しています" if running else ""
    if "[push]" in stdout and running:
        phase = "Playerへ転送・再生準備をしています"
    if meta.get("weekday") == "availability" and running:
        phase = "使用期限を更新しています"
    latest = next((line for line in reversed(stdout.splitlines()) if " start: " in line or " done (" in line), "")
    if latest:
        phase += " · " + latest.split(": ", 1)[-1]
    vision = meta.get("vision_id", "")
    return f'''<main class="standalone"><a class="inline-link" href="{url(vision,'publish')}">← Playerへの反映に戻る</a><section class="card job-detail"><span class="badge {"running" if running else "ok" if success else "err"}">{"処理中" if running else "完了" if success else "失敗"}</span><h1>{stage}</h1><p>{esc(phase)}</p><dl><dt>ビジョン</dt><dd>{esc(vision)}</dd><dt>反映先</dt><dd>{esc(meta.get('target',''))}</dd><dt>内容</dt><dd>{"使用期限のみ" if meta.get('weekday') == 'availability' else '素材と再生設定'}</dd></dl>{'<div class="progress-track"><span></span></div><p class="hint">この画面は自動で更新されます。電源を切らずにお待ちください。</p>' if running else '<p>素材一覧や再生設定へ戻って、次の変更を行えます。</p>' if success else '<div class="notice">接続先、SSHユーザー、素材ファイルを確認してください。下の詳細ログで原因を確認できます。</div>'}<details><summary>詳細ログ</summary><pre>{esc(stdout)}</pre><pre class="err">{esc(stderr)}</pre></details></section></main>'''
