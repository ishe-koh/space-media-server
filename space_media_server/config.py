import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class CabinetConfig:
    width: int
    height: int


@dataclass(frozen=True)
class ScreenConfig:
    cols: int
    rows: int


@dataclass(frozen=True)
class LanesConfig:
    cols: int
    rows: int


@dataclass(frozen=True)
class LanePolicy:
    fill: str
    align_x: str
    align_y: str
    image_duration_sec: int
    fps: int


@dataclass(frozen=True)
class EncodingConfig:
    video_codec: str
    crf: int
    preset: str
    pix_fmt: str
    audio_codec: str
    audio_bitrate: str
    audio_normalize: bool
    audio_normalize_filter: str


@dataclass(frozen=True)
class VisionConfig:
    cabinet: CabinetConfig
    screen: ScreenConfig
    lanes: LanesConfig
    lane_policy: LanePolicy
    encoding: EncodingConfig

    @property
    def screen_width(self) -> int:
        return self.cabinet.width * self.screen.cols

    @property
    def screen_height(self) -> int:
        return self.cabinet.height * self.screen.rows


def _require_int(obj: Dict[str, Any], key: str) -> int:
    value = obj.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be int")
    return value


def load_vision_config(path: Path) -> VisionConfig:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    cabinet_data = data.get("cabinet", {})
    screen_data = data.get("screen", {})
    lanes_data = data.get("lanes", {})
    lane_policy_data = data.get("lane_policy", {})
    encoding_data = data.get("encoding", {})

    cabinet = CabinetConfig(
        width=_require_int(cabinet_data, "width"),
        height=_require_int(cabinet_data, "height"),
    )
    screen = ScreenConfig(
        cols=_require_int(screen_data, "cols"),
        rows=_require_int(screen_data, "rows"),
    )
    lanes = LanesConfig(
        cols=_require_int(lanes_data, "cols"),
        rows=_require_int(lanes_data, "rows"),
    )

    lane_policy = LanePolicy(
        fill=str(lane_policy_data.get("fill", "contain")),
        align_x=str(lane_policy_data.get("align_x", "center")),
        align_y=str(lane_policy_data.get("align_y", "center")),
        image_duration_sec=int(lane_policy_data.get("image_duration_sec", 10)),
        fps=int(lane_policy_data.get("fps", 30)),
    )

    encoding = EncodingConfig(
        video_codec=str(encoding_data.get("video_codec", "libx264")),
        crf=int(encoding_data.get("crf", 18)),
        preset=str(encoding_data.get("preset", "veryfast")),
        pix_fmt=str(encoding_data.get("pix_fmt", "yuv420p")),
        audio_codec=str(encoding_data.get("audio_codec", "aac")),
        audio_bitrate=str(encoding_data.get("audio_bitrate", "128k")),
        audio_normalize=bool(encoding_data.get("audio_normalize", False)),
        audio_normalize_filter=str(
            encoding_data.get(
                "audio_normalize_filter",
                "loudnorm=I=-16:TP=-1.5:LRA=11",
            )
        ),
    )

    return VisionConfig(
        cabinet=cabinet,
        screen=screen,
        lanes=lanes,
        lane_policy=lane_policy,
        encoding=encoding,
    )
