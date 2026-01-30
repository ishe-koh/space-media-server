#!/usr/bin/env python3
import json
from pathlib import Path


WEEKDAYS = ["always", "mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or (default or "")


def _prompt_int(text: str, default: int) -> int:
    while True:
        value = _prompt(text, str(default))
        try:
            return int(value)
        except ValueError:
            print("Enter a number.")


def _prompt_yes(text: str, default: bool = False) -> bool:
    d = "y" if default else "n"
    while True:
        value = _prompt(text, d).lower()
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("Enter y or n.")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    vision_id = _prompt("VISION_ID", "sample_vision_player")

    weekday = _prompt("weekday (always/mon/tue/...) ", "always").lower()
    if weekday not in WEEKDAYS:
        print(f"Invalid weekday: {weekday}")
        return

    default_volume = _prompt_int("meta.default_volume", 100)
    default_loop = _prompt_yes("meta.default_loop (y/n)", True)
    default_offset = _prompt_int("meta.default_start_offset_sec", 0)

    use_auto = _prompt_yes("Use auto_policy? (y/n)", False)
    auto_policy = {}
    if use_auto:
        directory = _prompt("auto_policy.directory (ex: media/always)", f"media/{weekday}")
        sort = _prompt("auto_policy.sort (asc/desc)", "asc")
        mode = _prompt("auto_policy.mode (replace_if_empty/append_remaining/disabled)", "replace_if_empty")
        default_ext = ".mp4,.mov,.m4v,.mkv,.webm,.avi,.mpg,.mpeg,.png,.jpg,.jpeg,.bmp,.webp"
        ext_raw = _prompt("auto_policy.extensions (comma separated)", default_ext)
        extensions = [e.strip() for e in ext_raw.split(",") if e.strip()]
        auto_policy = {
            "directory": directory,
            "sort": sort,
            "mode": mode,
            "extensions": extensions,
        }

    lane_count = _prompt_int("lane count", 1)
    lanes: dict[str, dict] = {}

    for i in range(lane_count):
        lane_id = f"lane{i}"
        print(f"\n--- {lane_id} ---")
        lane_conf: dict = {}
        if _prompt_yes("Set volume for this lane? (y/n)", False):
            lane_conf["volume"] = _prompt_int("volume", default_volume)

        items: list = []
        print("Add items for this lane. Leave source empty to finish.")
        while True:
            source = _prompt("source (relative to source/)", "")
            if not source:
                break
            item: dict = {"source": source}
            if _prompt_yes("Is this an image (needs duration_sec)? (y/n)", False):
                item["duration_sec"] = _prompt_int("duration_sec", 10)
            items.append(item if len(item) > 1 else source)

        if items:
            lane_conf["items"] = items

        lanes[lane_id] = lane_conf

    playlist = {
        "meta": {
            "default_volume": default_volume,
            "default_loop": default_loop,
            "default_start_offset_sec": default_offset,
        },
        "lanes": lanes,
    }
    if auto_policy:
        playlist["auto_policy"] = auto_policy

    playlists_dir = repo_root / "vision_players" / vision_id / "source" / "playlists"
    playlists_dir.mkdir(parents=True, exist_ok=True)
    out_path = playlists_dir / f"{weekday}.json"
    out_path.write_text(json.dumps(playlist, indent=2), encoding="utf-8")

    print(f"\nWritten: {out_path}")


if __name__ == "__main__":
    main()
