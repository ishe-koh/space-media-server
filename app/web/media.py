from email.parser import BytesParser
from email.policy import default as email_default
from pathlib import Path
from typing import Dict, Tuple


def parse_multipart(headers, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    content_type = headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        return {}, {}
    msg = BytesParser(policy=email_default).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8") + body
    )
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_param("filename", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = (filename, payload)
        else:
            fields[name] = payload.decode("utf-8", errors="ignore")
    return fields, files


def save_upload(vision_root: Path, vision_id: str, weekday: str, filename: str, content: bytes) -> Path:
    if not filename:
        raise ValueError("filename missing")
    filename = Path(filename).name
    dest_dir = vision_root / vision_id / "source" / "media" / weekday
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    with dest_path.open("wb") as f:
        f.write(content)
    return dest_path


def list_media_dirs(vision_root: Path, vision_id: str) -> dict[str, list[Path]]:
    media_root = vision_root / vision_id / "source" / "media"
    if not media_root.exists():
        return {}
    result: dict[str, list[Path]] = {}
    for p in sorted(media_root.iterdir()):
        if not p.is_dir():
            continue
        files = sorted([f for f in p.iterdir() if f.is_file()])
        result[p.name] = files
    return result


def read_playlist(vision_root: Path, vision_id: str, weekday: str) -> Tuple[Path, str]:
    playlist_path = vision_root / vision_id / "source" / "playlists" / f"{weekday}.json"
    if not playlist_path.exists():
        return playlist_path, ""
    return playlist_path, playlist_path.read_text(encoding="utf-8")


def list_output_media(vision_root: Path, vision_id: str, weekday: str) -> dict[str, list[Path]]:
    output_root = vision_root / vision_id / "output" / "media" / weekday
    if not output_root.exists():
        return {}
    lanes: Dict[str, list[Path]] = {}
    for lane_dir in sorted([p for p in output_root.iterdir() if p.is_dir()]):
        files = sorted([f for f in lane_dir.iterdir() if f.is_file()])
        lanes[lane_dir.name] = files
    return lanes
