#!/usr/bin/env python3
import argparse
import shutil
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize a new vision directory from sample template")
    parser.add_argument("vision_id", type=str, help="new vision id (ex: akiba_01)")
    parser.add_argument("--template", type=Path, default=Path("./vision_players/sample_vision_player"))
    parser.add_argument("--media-root", type=Path, default=Path("./vision_players"))
    parser.add_argument("--force", action="store_true", help="overwrite existing vision directory")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dest = args.media_root / args.vision_id

    if dest.exists():
        if not args.force:
            raise SystemExit(f"{dest} already exists; use --force to overwrite")
        shutil.rmtree(dest)

    if not args.template.exists():
        raise SystemExit(f"template not found: {args.template}")

    shutil.copytree(args.template, dest)
    print(f"[init] created {dest}")


if __name__ == "__main__":
    main()
