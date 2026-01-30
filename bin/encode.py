#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.encoding_pipeline import encode_playlist


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encode source playlists for vision-player")
    parser.add_argument("--vision-id", type=str, help="vision id (ex: sample_vision_player)")
    parser.add_argument("--playlist", type=Path, help="source playlist JSON path")
    parser.add_argument("--all", action="store_true", help="encode all playlists in media/<vision>/source/playlists")
    parser.add_argument("--config", type=Path, help="override config path (default: media/<vision>/config/vision_config.json)")
    parser.add_argument("--source-root", type=Path, help="override source root (default: media/<vision>/source)")
    parser.add_argument("--encoded-dir", type=Path, help="override output media dir (default: vision_players/<vision>/output/media)")
    parser.add_argument("--playlists-dir", type=Path, help="override playlists dir (default: media/<vision>/out/playlists)")
    parser.add_argument("--ffmpeg", type=str, default="ffmpeg")
    parser.add_argument("--weekday", type=str, help="override weekday (mon,tue,...) for playlist output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _choose_vision_id(repo_root: Path) -> str:
    vision_root = repo_root / "vision_players"
    if not vision_root.exists():
        raise SystemExit("vision_players directory not found. Run: ./bin/init_vision.py <vision_id>")
    dirs = sorted([p.name for p in vision_root.iterdir() if p.is_dir()])
    if not dirs:
        raise SystemExit("no vision_players found. Run: ./bin/init_vision.py <vision_id>")
    if not sys.stdin.isatty():
        raise SystemExit("Set --vision-id (TTY required for interactive selection)")
    print("Select VISION_ID:")
    for i, name in enumerate(dirs, start=1):
        print(f"  [{i}] {name}")
    choice = input("Choose number: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(dirs)):
        raise SystemExit("Invalid choice.")
    return dirs[int(choice) - 1]


def _choose_playlist() -> tuple[bool, Path | None]:
    if not sys.stdin.isatty():
        return True, None
    print("Playlist not specified.")
    print("Select weekday to encode, or 'all':")
    options = [
        "all",
        "always",
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
    ]
    for i, name in enumerate(options, start=1):
        print(f"  [{i}] {name}")
    choice = input("Choose number: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(options)):
        raise SystemExit("Invalid choice.")
    selected = options[int(choice) - 1]
    if selected == "all":
        return True, None
    return False, Path(f"source/playlists/{selected}.json")


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
    if not args.vision_id:
        args.vision_id = _choose_vision_id(REPO_ROOT)

    if not args.all and not args.playlist:
        args.all, args.playlist = _choose_playlist()

    if args.playlist and not args.playlist.is_absolute():
        playlist_str = str(args.playlist)
        if playlist_str.startswith("source/playlists/"):
            args.playlist = REPO_ROOT / "vision_players" / args.vision_id / playlist_str
        else:
            args.playlist = REPO_ROOT / args.playlist

    paths = _resolve_paths(args)
    print(f"[encode] vision_id={args.vision_id}")
    if args.all:
        print("[encode] mode=all")
    else:
        print(f"[encode] playlist={args.playlist}")

    if not paths["config"].exists():
        raise SystemExit(
            f"config not found: {paths['config']}\n"
            f"Run: ./bin/init_vision.py {args.vision_id}"
        )
    if not paths["source_root"].exists():
        raise SystemExit(
            f"source root not found: {paths['source_root']}\n"
            f"Run: ./bin/init_vision.py {args.vision_id}"
        )

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
    if not args.playlist.exists():
        raise SystemExit(f"playlist not found: {args.playlist}")

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
