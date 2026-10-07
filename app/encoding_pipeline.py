import json
import hashlib
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.config_loader import LanePolicy, VisionConfig, load_vision_config
from app.layout_calc import Rect, calc_lane_rects
from app.limited_media import limited_items, load_media_rules, refresh_output_availability


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpg", ".mpeg"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class EncodeItem:
    source_path: Path
    output_path: Path
    is_image: bool
    duration_sec: Optional[int]
    extra_fields: Dict[str, str]
    policy_override: Dict[str, str]


@dataclass(frozen=True)
class EncodePlan:
    weekday: str
    playlist_out: Path
    items: List[EncodeItem]
    playlist_json: Dict
    availability: Dict
    source_root: Path


def expand_active_time_always(playlist: Dict) -> None:
    active_time = playlist.get("active_time")
    if not isinstance(active_time, dict) or "always" not in active_time:
        return

    always = active_time["always"]
    expanded = {weekday: active_time.get(weekday, always) for weekday in WEEKDAYS}
    playlist["active_time"] = expanded


def _align_expr(align: str, total: str, inner: str) -> str:
    if align == "left" or align == "top":
        return "0"
    if align == "right" or align == "bottom":
        return f"{total}-{inner}"
    return f"({total}-{inner})/2"


_ANCHOR_MAP = {
    "top_left": ("left", "top"),
    "top": ("center", "top"),
    "top_right": ("right", "top"),
    "left": ("left", "center"),
    "center": ("center", "center"),
    "right": ("right", "center"),
    "bottom_left": ("left", "bottom"),
    "bottom": ("center", "bottom"),
    "bottom_right": ("right", "bottom"),
}


def _merge_policy(base: LanePolicy, override: Dict[str, str]) -> LanePolicy:
    align_x = override.get("align_x", base.align_x)
    align_y = override.get("align_y", base.align_y)
    fill = override.get("fill", base.fill)
    image_duration_sec = int(override.get("image_duration_sec", base.image_duration_sec))
    fps = int(override.get("fps", base.fps))
    return LanePolicy(
        fill=str(fill),
        align_x=str(align_x),
        align_y=str(align_y),
        image_duration_sec=image_duration_sec,
        fps=fps,
    )


def _policy_from_anchor(override: Dict[str, str]) -> Dict[str, str]:
    anchor = override.get("anchor")
    if not anchor:
        return override
    anchor_key = str(anchor).lower()
    if anchor_key not in _ANCHOR_MAP:
        raise ValueError(f"unknown anchor: {anchor}")
    align_x, align_y = _ANCHOR_MAP[anchor_key]
    merged = dict(override)
    merged["align_x"] = align_x
    merged["align_y"] = align_y
    return merged


def build_filter(rect: Rect, policy: LanePolicy) -> str:
    if policy.fill == "stretch":
        return f"scale={rect.width}:{rect.height}"

    if policy.fill == "cover":
        # scale up then crop
        crop_x = _align_expr(policy.align_x, "iw", "ow")
        crop_y = _align_expr(policy.align_y, "ih", "oh")
        return (
            f"scale={rect.width}:{rect.height}:force_original_aspect_ratio=increase,"
            f"crop={rect.width}:{rect.height}:{crop_x}:{crop_y}"
        )

    # default: contain (letterbox)
    pad_x = _align_expr(policy.align_x, "ow", "iw")
    pad_y = _align_expr(policy.align_y, "oh", "ih")
    return (
        f"scale={rect.width}:{rect.height}:force_original_aspect_ratio=decrease,"
        f"pad={rect.width}:{rect.height}:{pad_x}:{pad_y}:black"
    )


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _build_auto_items(
    auto_policy: Dict,
    source_root: Path,
) -> List[Dict[str, str]]:
    directory = auto_policy.get("directory")
    if not directory:
        return []

    base_dir = Path(directory)
    if not base_dir.is_absolute():
        base_dir = source_root / base_dir

    if not base_dir.exists():
        print(f"[encoder] auto_policy directory not found: {base_dir}")
        return []

    extensions = auto_policy.get(
        "extensions",
        sorted(list(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS)),
    )
    allowed_ext = {ext.lower() for ext in extensions}

    files = [
        p for p in base_dir.iterdir()
        if p.is_file() and p.suffix.lower() in allowed_ext
    ]

    sort_order = auto_policy.get("sort", "asc")
    files.sort(reverse=(sort_order == "desc"))

    items: List[Dict[str, str]] = []
    for path in files:
        rel = path.relative_to(source_root)
        items.append({"source": str(rel)})
    return items


def _merge_items(
    lane_items: List,
    auto_items: List[Dict[str, str]],
    mode: str,
    source_root: Path,
) -> List:
    if mode == "disabled":
        return lane_items
    if mode == "replace_if_empty":
        return auto_items if not lane_items else lane_items
    if mode == "append_remaining":
        lane_paths = set()
        for item in lane_items:
            if isinstance(item, str):
                lane_paths.add((source_root / item).resolve())
            elif isinstance(item, dict):
                source = item.get("source") or item.get("path")
                if source:
                    lane_paths.add((source_root / source).resolve())
        remaining = [
            item for item in auto_items
            if (source_root / item["source"]).resolve() not in lane_paths
        ]
        return lane_items + remaining
    raise ValueError(f"Unknown auto_policy.mode: {mode}")


def _parse_item(
    item,
    source_root: Path,
) -> Tuple[Path, Optional[int], Dict[str, str], Dict[str, str]]:
    if isinstance(item, str):
        return source_root / item, None, {}, {}

    if isinstance(item, dict):
        source = item.get("source") or item.get("path")
        if not source:
            raise ValueError("item requires 'source' or 'path'")
        duration = item.get("duration_sec")
        extra = {}
        for key in ("is_available_from", "is_available_until"):
            if key in item:
                extra[key] = item[key]
        policy_override = item.get("policy", {})
        if not isinstance(policy_override, dict):
            raise ValueError("item.policy must be an object")
        return source_root / source, duration, extra, policy_override

    raise ValueError("item must be string or object")


def _build_output_name(source_path: Path, auto_index: int) -> tuple[str, int]:
    stem = source_path.stem
    suffix = ".mp4"
    match = re.match(r"^([0-9]+)[-_ ]*(.*)$", stem)
    if match:
        num = int(match.group(1))
        rest = match.group(2)
        if rest:
            return f"{num:03d}_{rest}{suffix}", auto_index
        return f"{num:03d}{suffix}", auto_index
    return f"{auto_index:03d}_{stem}{suffix}", auto_index + 1


def _strip_media_prefix(directory: str) -> Path:
    rel_dir = Path(directory)
    parts = rel_dir.parts
    if parts and parts[0] == "media":
        rel_dir = Path(*parts[1:]) if len(parts) > 1 else Path()
    return rel_dir


def _map_auto_directory_for_output(directory: str, lane_id: str) -> Path:
    rel_dir = _strip_media_prefix(directory)
    return rel_dir / lane_id


def _build_encode_items(
    playlist: Dict, source_root: Path, encoded_dir: Path, weekday: str,
    config: VisionConfig,
) -> Tuple[List[EncodeItem], Dict]:
    output = dict(playlist)
    output.pop("auto_policy", None)
    output["media_availability"] = "../media_availability.json"
    expand_active_time_always(output)
    output_lanes = {}
    encoded = {}
    rules = load_media_rules(source_root)

    for lane_id, lane in playlist.get("lanes", {}).items():
        lane_output = dict(lane)
        policy = dict(lane.get("auto_policy", playlist.get("auto_policy", {})))
        mode = policy.get("mode", "replace_if_empty")
        automatic = _build_auto_items(policy, source_root)
        if (mode != "disabled" and weekday in WEEKDAYS
                and policy.get("directory") == f"media/{weekday}" and not automatic):
            policy["directory"] = "media/always"
            automatic = _build_auto_items(policy, source_root)
        items = _merge_items(lane.get("items") or [], automatic, mode, source_root)
        common = limited_items(source_root, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS)
        common_sources = {(source_root / item["source"]).resolve() for item in common}
        items = [item for item in items if _parse_item(item, source_root)[0].resolve() not in common_sources]
        limited_mode = playlist.get("weekday_limited_mode", "weekday_plus_limited")
        if limited_mode not in {"weekday_only", "weekday_plus_limited"}:
            raise ValueError("unknown weekday_limited_mode")
        has_weekday = weekday in WEEKDAYS and bool(_build_auto_items({"directory": f"media/{weekday}"}, source_root))
        if not (has_weekday and limited_mode == "weekday_only"):
            items += common

        def register(item):
            source, duration, extra, override = _parse_item(item, source_root)
            source_id = source.relative_to(source_root).as_posix()
            effective_policy = {**lane.get("lane_policy", {}), **override}
            if not isinstance(effective_policy, dict):
                raise ValueError("lane_policy must be an object")
            # Availability and playlist position do not change encoded video bytes.
            stat = source.stat() if source.exists() else None
            signature = json.dumps({"source": source_id, "config": asdict(config),
                "policy": effective_policy, "duration": duration,
                "mtime": stat.st_mtime_ns if stat else None, "size": stat.st_size if stat else None}, sort_keys=True)
            variant = hashlib.sha256(signature.encode()).hexdigest()[:20]
            group = Path(source_id).parts[1] if Path(source_id).parts[:1] == ("media",) and len(Path(source_id).parts) > 2 else "explicit"
            name, _ = _build_output_name(source, 900)
            destination = encoded_dir / group / variant / lane_id / name
            if destination not in encoded:
                encoded[destination] = EncodeItem(source, destination, _is_image(source), duration,
                                                 rules.get(source_id, extra), effective_policy)
            return destination.relative_to(encoded_dir).as_posix()

        lane_output["items"] = list(dict.fromkeys(register(item) for item in items))
        if policy.get("directory") and mode != "disabled":
            lane_output["auto_policy"] = {**policy,
                "directory": str(_map_auto_directory_for_output(policy["directory"], lane_id)),
                "items": list(dict.fromkeys(register(item) for item in automatic))}
        else:
            lane_output.pop("auto_policy", None)
        output_lanes[lane_id] = lane_output
    output["lanes"] = output_lanes
    return list(encoded.values()), output


def _build_ffmpeg_cmd(
    item: EncodeItem,
    rect: Rect,
    config: VisionConfig,
    ffmpeg_path: str,
) -> List[str]:
    policy_override = _policy_from_anchor(item.policy_override)
    policy = _merge_policy(config.lane_policy, policy_override)
    encoding = config.encoding

    vf = build_filter(rect, policy)

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
    ]

    if item.is_image:
        duration = item.duration_sec or policy.image_duration_sec
        cmd += [
            "-loop",
            "1",
            "-t",
            str(duration),
        ]

    cmd += [
        "-i",
        str(item.source_path),
        "-vf",
        vf,
        "-r",
        str(policy.fps),
        "-c:v",
        encoding.video_codec,
        "-crf",
        str(encoding.crf),
        "-preset",
        encoding.preset,
        "-pix_fmt",
        encoding.pix_fmt,
        "-movflags",
        "+faststart",
    ]

    if item.is_image:
        cmd += ["-an"]
    else:
        if encoding.audio_normalize:
            cmd += ["-af", encoding.audio_normalize_filter]
        cmd += [
            "-c:a",
            encoding.audio_codec,
            "-b:a",
            encoding.audio_bitrate,
        ]

    cmd.append(str(item.output_path))
    return cmd


def build_encode_plan(
    playlist_path: Path,
    config_path: Path,
    source_root: Path,
    encoded_dir: Path,
    playlists_dir: Path,
    weekday: Optional[str] = None,
) -> EncodePlan:
    config = load_vision_config(config_path)

    with playlist_path.open("r", encoding="utf-8") as f:
        playlist = json.load(f)

    lane_count = config.lanes.cols * config.lanes.rows
    expected_lane_ids = [f"lane{i}" for i in range(lane_count)]
    lanes = playlist.get("lanes", {})
    if not isinstance(lanes, dict):
        raise ValueError("playlist.lanes must be an object")
    normalized_lanes = {}
    for lane_id in expected_lane_ids:
        normalized_lanes[lane_id] = lanes.get(lane_id, {"items": []})
    playlist["lanes"] = normalized_lanes

    resolved_weekday = weekday or playlist_path.stem

    # Embed screen info to keep player stateless
    playlist_screen = {
        "width": config.screen_width,
        "height": config.screen_height,
        "cols": config.lanes.cols,
        "rows": config.lanes.rows,
    }
    playlist["screen"] = playlist_screen

    items, output_playlist = _build_encode_items(
        playlist=playlist,
        source_root=source_root,
        encoded_dir=encoded_dir,
        weekday=resolved_weekday,
        config=config,
    )

    playlists_dir.mkdir(parents=True, exist_ok=True)
    playlist_out = playlists_dir / f"{resolved_weekday}.json"

    return EncodePlan(
        weekday=resolved_weekday,
        playlist_out=playlist_out,
        items=items,
        playlist_json=output_playlist,
        source_root=source_root,
        availability={item.output_path.relative_to(encoded_dir).as_posix(): {
            "source": item.source_path.relative_to(source_root).as_posix(),
            "legacy_window": item.extra_fields,
        } for item in items},
    )


def encode_plan(
    plan: EncodePlan,
    config: VisionConfig,
    ffmpeg_path: str = "ffmpeg",
    dry_run: bool = False,
    overwrite: bool = False,
) -> None:
    rects = calc_lane_rects(
        screen_width=config.screen_width,
        screen_height=config.screen_height,
        cols=config.lanes.cols,
        rows=config.lanes.rows,
    )

    if len(rects) < len(plan.playlist_json.get("lanes", {})):
        raise ValueError("lane count exceeds rects")

    for index, (lane_id, _lane_conf) in enumerate(plan.playlist_json["lanes"].items()):
        rect = rects[index]
        lane_items = [i for i in plan.items if i.output_path.parts[-2] == lane_id]

        for item_index, item in enumerate(lane_items, start=1):
            progress = f"[encode] {plan.weekday}/{lane_id} {item_index}/{len(lane_items)}"
            if item.output_path.exists() and not overwrite:
                print(f"{progress} skip existing: {item.output_path.name}", flush=True)
                continue
            if not item.source_path.exists():
                raise FileNotFoundError(f"missing source: {item.source_path}")
            if dry_run:
                print("[dry-run]", item.source_path, "->", item.output_path)
                continue

            item.output_path.parent.mkdir(parents=True, exist_ok=True)
            cmd = _build_ffmpeg_cmd(item, rect, config, ffmpeg_path)
            print(f"{progress} start: {item.source_path.name}", flush=True)
            started = time.monotonic()
            subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
            print(f"{progress} done ({time.monotonic() - started:.1f}s): {item.output_path.name}", flush=True)

    if dry_run:
        return

    refresh_output_availability(
        source_root=plan.source_root,
        output_root=plan.playlist_out.parent.parent,
        entries=plan.availability,
    )
    plan.playlist_out.write_text(
        json.dumps(plan.playlist_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[encode] playlist complete: {plan.weekday}", flush=True)


def encode_playlist(
    playlist_path: Path,
    config_path: Path,
    source_root: Path,
    encoded_dir: Path,
    playlists_dir: Path,
    ffmpeg_path: str = "ffmpeg",
    weekday: Optional[str] = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> None:
    config = load_vision_config(config_path)
    plan = build_encode_plan(
        playlist_path=playlist_path,
        config_path=config_path,
        source_root=source_root,
        encoded_dir=encoded_dir,
        playlists_dir=playlists_dir,
        weekday=weekday,
    )
    print(f"[encode] playlist: {playlist_path}")
    print(f"[encode] output playlist: {plan.playlist_out}")
    print(f"[encode] items: {len(plan.items)}")
    items_by_lane: Dict[str, List[EncodeItem]] = {}
    for item in plan.items:
        lane_id = item.output_path.parent.name
        items_by_lane.setdefault(lane_id, []).append(item)
    for lane_id in sorted(items_by_lane.keys()):
        print(f"[encode] lane {lane_id} ({len(items_by_lane[lane_id])})")
        for item in items_by_lane[lane_id]:
            print(f"[encode]   {item.source_path.name} -> {item.output_path.name}")
    encode_plan(
        plan=plan,
        config=config,
        ffmpeg_path=ffmpeg_path,
        dry_run=dry_run,
        overwrite=overwrite,
    )
