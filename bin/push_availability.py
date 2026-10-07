#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.limited_media import refresh_output_availability
vision = os.environ["VISION_ID"]
base = ROOT / "vision_players" / vision
if not (base / "output/media_availability.json").exists():
    raise SystemExit("最初に Encode + Push を all で実行してください")
refresh_output_availability(base / "source", base / "output")
env = {**os.environ, "PUSH_AVAILABILITY_ONLY": "1"}
subprocess.run([str(ROOT / "bin/push_media.sh")], cwd=ROOT, env=env, check=True)
