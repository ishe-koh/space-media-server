# space-media-server

media-server is the "factory" side of the system. It **never plays media** and only:
1) manages source assets
2) encodes them for the LED cabinet layout
3) outputs playlists + encoded media for vision-player to pull via rsync

Design intent: **make the player dumb, make the server smart**.

## Responsibilities boundary
- **media-server**: cabinet/screen/lane aware encoding, crop/scale/pad, static image to video, playlist generation
- **vision-player**: playback only (mpv), weekday selection, lane control, updating flag handling

## Directory layout
```
space-media-server/
├─ media/
│  └─ akiba_01/
│     ├─ config/
│     │  └─ vision_config.json
│     ├─ source/
│     │  ├─ raw/             # source videos/images
│     │  └─ playlists/       # source playlists (server format)
│     └─ out/
│        ├─ encoded/          # output media (rsync to player)
│        └─ playlists/        # output playlists (rsync to player)
├─ system_media/
└─ tools/
   └─ encode.py
   └─ init_vision.py
   └─ emit_sync_env.py
```

## LED cabinet model (summary)
- **cabinet**: smallest physical LED unit (fixed pixel size)
- **screen**: cabinet grid (cols × rows)
- **lane**: logical playback regions inside the screen
- Server computes screen size and lane rects; player just displays those outputs.

## Source playlist format (server)
Based on the vision-player playlist schema, with a small extension:
- items can use `source` (path under `source/`) instead of `path`
- images can specify `duration_sec`
- `lane_policy` can be set per lane
- `policy` can be set per item (override lane + global)
- `auto_policy` can be set at top-level or per-lane to pull items from a folder

Example: `media/akiba_01/source/playlists/always.json`
```json
{
  "meta": {"default_volume": 100, "default_loop": true},
  "auto_policy": {
    "directory": "raw/always",
    "mode": "replace_if_empty",
    "sort": "asc",
    "extensions": [".mp4", ".png", ".jpg"]
  },
  "lanes": {
    "lane0": {
      "lane_policy": {
        "fill": "contain",
        "anchor": "top_left"
      },
      "items": [
        {"source": "raw/movie_a.mp4"},
        {
          "source": "raw/poster_a.png",
          "duration_sec": 12,
          "policy": {"anchor": "center"}
        }
      ]
    }
  }
}
```

JSONC sample (with comments):
- `media/_template/source/playlists/sample.jsonc`

## Output playlist format (player)
`media/<vision_id>/out/playlists/<weekday>.json` is generated for the player:
- items become `path` relative to `encoded/`
- `is_available_from` / `is_available_until` are passed through
- `screen` is embedded so the player can stay stateless

## Encoding policy
- **images** are converted to H.264 MP4 using `duration_sec` (or default)
- **videos** are resized to lane rect size according to `lane_policy.fill`
  - `contain`: letterbox (scale + pad)
  - `cover`: crop (scale + crop)
  - `stretch`: scale without preserving aspect
  - `anchor` can be one of: `top_left`, `top`, `top_right`, `left`, `center`,
    `right`, `bottom_left`, `bottom`, `bottom_right`
- `auto_policy` rules (server-side):
  - `directory`: folder relative to `source/` (ex: `raw/always`)
  - `mode`:
    - `replace_if_empty`: use auto items only when lane items are empty
    - `append_remaining`: append files not already listed in items
    - `disabled`: no auto items
- audio normalization can be enabled via `encoding.audio_normalize`
  - default filter: `loudnorm=I=-16:TP=-1.5:LRA=11`

## Usage
Encode one playlist:
```
./tools/encode.py --vision-id akiba_01 --playlist ./media/akiba_01/source/playlists/always.json
```

Encode all playlists:
```
./tools/encode.py --vision-id akiba_01 --all
```

Initialize a new vision directory:
```
./tools/init_vision.py shibuya_02
```

Generate sync_media.env snippet for vision-player:
```
./tools/emit_sync_env.py akiba_01 --remote-host 10.0.0.2
```

sync_media.env sample (for vision-player):
- `media/_template/config/sample_sync_media.env`
- copy to `space-vision-player/config/sync_media.env` and edit values

Push from media-server using DHCP lease lookup:
```
VISION_ID=akiba_01 ./tools/push_media.sh
```

Options:
- `--vision-id` (required)
- `--config` (default: `media/<vision>/config/vision_config.json`)
- `--source-root` (default: `media/<vision>/source`)
- `--encoded-dir` (default: `media/<vision>/out/encoded`)
- `--playlists-dir` (default: `media/<vision>/out/playlists`)
- `--ffmpeg` (default: `ffmpeg`)
- `--overwrite` to re-encode existing outputs
- `--dry-run` to preview outputs

## Requirements
- FFmpeg installed and available on PATH

## Notes
- media-server does **not** push; vision-player pulls via rsync.
- Keep `media/<vision>/out` as deploy artifacts.
