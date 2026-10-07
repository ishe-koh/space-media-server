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
    windows = load_windows(source_root)
    if not directory.exists():
        return []
    items = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        window = windows.get(path.name)
        if not window:
            print(f"[encoder] limited素材は期限未設定のためスキップ: {path.name}")
            continue
        window = validate_window(window.get("is_available_from", ""), window.get("is_available_until", ""))
        items.append({"source": str(path.relative_to(source_root)), **window})
    return items
