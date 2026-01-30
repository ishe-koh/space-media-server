#!/usr/bin/env python3
import argparse
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit sync_media.env snippet for vision-player")
    parser.add_argument("vision_id", type=str, help="vision id (ex: akiba_01)")
    parser.add_argument("--remote-user", type=str, default="pi")
    parser.add_argument("--remote-host", type=str, required=True)
    parser.add_argument("--repo-path", type=Path, default=Path("/srv/space-media-server"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    remote_base = args.repo_path / "vision_players" / args.vision_id / "output"

    print(f"REMOTE_USER={args.remote_user}")
    print(f"REMOTE_HOST={args.remote_host}")
    print(f"REMOTE_BASE={remote_base}")


if __name__ == "__main__":
    main()
