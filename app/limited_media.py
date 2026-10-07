"""Shared availability settings for source/media/is_limited files."""
import json
from datetime import datetime
from pathlib import Path


def validate_window(start: str, end: str) -> dict:
    if not end:
        raise ValueError("limited素材には放映終了日時が必要です")
    values = {"is_available_until": end}
    if start:
        values["is_available_from"] = start
    parsed = {}
    for key, value in values.items():
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError("日時にはタイムゾーンを指定してください（例: +09:00）")
        parsed[key] = dt
    if start and parsed["is_available_from"] > parsed["is_available_until"]:
        raise ValueError("開始日時は終了日時以前にしてください")
    return values


def load_windows(source_root: Path) -> dict:
    path = source_root / "limited_media.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_window(source_root: Path, filename: str, start: str, end: str) -> None:
    values = validate_window(start, end)
    windows = load_windows(source_root)
    windows[Path(filename).name] = values
    path = source_root / "limited_media.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(windows, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def limited_items(source_root: Path, extensions) -> list:
    directory = source_root / "media" / "is_limited"
    if not directory.exists():
        return []
    return [{"source": path.relative_to(source_root).as_posix()}
            for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix.lower() in extensions]


def load_media_rules(source_root: Path) -> dict:
    """One record per source asset; central records override legacy playlist dates."""
    rules = {f"media/is_limited/{name}": window for name, window in load_windows(source_root).items()}
    central = source_root / "media_availability.json"
    if central.exists():
        rules.update(json.loads(central.read_text(encoding="utf-8")))
    authoritative = set(rules)
    legacy = {}
    for playlist_path in sorted((source_root / "playlists").glob("*.json")):
        playlist = json.loads(playlist_path.read_text(encoding="utf-8"))
        for lane in playlist.get("lanes", {}).values():
            for item in lane.get("items", []):
                if not isinstance(item, dict):
                    continue
                source = item.get("source") or item.get("path")
                window = {k: item[k] for k in ("is_available_from", "is_available_until") if k in item}
                if not source or not window:
                    continue
                # Central settings are authoritative; inconsistent legacy dates need migration.
                if source in authoritative:
                    continue
                if source in legacy and legacy[source] != window:
                    raise ValueError(f"素材 {source} の期限がプレイリスト間で異なります。素材の使用可能期間を共通設定してください")
                legacy[source] = window
                rules[source] = window
    return rules


def save_media_rule(source_root: Path, source: str, start: str, end: str) -> None:
    path = Path(source)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "media":
        raise ValueError("invalid media path")
    if path.parts[1:2] == ("is_limited",):
        window = validate_window(start, end)
    else:
        window = {k: v for k, v in (("is_available_from", start), ("is_available_until", end)) if v}
        parsed = [datetime.fromisoformat(v) for v in window.values()]
        if any(dt.tzinfo is None for dt in parsed):
            raise ValueError("日時にはタイムゾーンを指定してください")
        if start and end and datetime.fromisoformat(start) > datetime.fromisoformat(end):
            raise ValueError("開始日時は終了日時以前にしてください")
    target = source_root / "media_availability.json"
    records = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    records[path.as_posix()] = window
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def refresh_output_availability(source_root: Path, output_root: Path, entries=None) -> Path:
    target = output_root / "media_availability.json"
    current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"version": 1, "items": {}}
    if entries is not None:
        current["items"].update(entries)
    rules = load_media_rules(source_root)
    for record in current["items"].values():
        source = record["source"]
        inline = record.get("legacy_window", {})
        window = rules.get(source, inline)
        for key in ("is_available_from", "is_available_until", "enabled"):
            record.pop(key, None)
        record.update(window)
        if source.startswith("media/is_limited/") and not window.get("is_available_until"):
            record["enabled"] = False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target
