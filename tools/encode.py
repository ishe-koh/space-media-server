#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from space_media_server.encoder import encode_playlist


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encode source playlists for vision-player")
    parser.add_argument("--vision-id", type=str, required=True, help="vision id (ex: sample_vision_player)")
    parser.add_argument("--playlist", type=Path, help="source playlist JSON path")
    parser.add_argument("--all", action="store_true", help="encode all playlists in media/<vision>/source/playlists")
    parser.add_argument("--config", type=Path, help="override config path (default: media/<vision>/config/vision_config.json)")
    parser.add_argument("--source-root", type=Path, help="override source root (default: media/<vision>/source)")
    parser.add_argument("--encoded-dir", type=Path, help="override encoded dir (default: media/<vision>/out/encoded)")
    parser.add_argument("--playlists-dir", type=Path, help="override playlists dir (default: media/<vision>/out/playlists)")
    parser.add_argument("--ffmpeg", type=str, default="ffmpeg")
    parser.add_argument("--weekday", type=str, help="override weekday (mon,tue,...) for playlist output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _resolve_paths(args: argparse.Namespace) -> dict:
    base_dir = Path("./vision_players") / args.vision_id
    return {
        "config": args.config or (base_dir / "config" / "vision_config.json"),
        "source_root": args.source_root or (base_dir / "source"),
        "encoded_dir": args.encoded_dir or (base_dir / "output" / "media"),
        "playlists_dir": args.playlists_dir or (base_dir / "output" / "playlists"),
    }


def main() -> None:
    args = _parse_args()
    paths = _resolve_paths(args)

    if args.all:
        playlists_dir = paths["source_root"] / "playlists"
        playlist_paths = sorted(playlists_dir.glob("*.json"))
        if not playlist_paths:
            raise SystemExit(f"no playlists found in {playlists_dir}")
        for playlist_path in playlist_paths:
            encode_playlist(
                playlist_path=playlist_path,
                config_path=paths["config"],
                source_root=paths["source_root"],
                encoded_dir=paths["encoded_dir"],
                playlists_dir=paths["playlists_dir"],
                ffmpeg_path=args.ffmpeg,
                weekday=None,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
            )
        return

    if not args.playlist:
        raise SystemExit("--playlist is required unless --all is set")

    encode_playlist(
        playlist_path=args.playlist,
        config_path=paths["config"],
        source_root=paths["source_root"],
        encoded_dir=paths["encoded_dir"],
        playlists_dir=paths["playlists_dir"],
        ffmpeg_path=args.ffmpeg,
        weekday=args.weekday,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
