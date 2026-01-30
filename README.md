# space-media-server

LEDビジョン向けのメディア生成サーバー。
source をエンコードして `output/` を作り、vision-player に配布する。

## What this does
- 素材（動画/静止画）を管理
- cabinet/screen/lane に合わせてエンコード
- 曜日別 playlist を生成
- vision-player へ push（rsync）

## Requirements
- OS: Debian / Raspberry Pi OS
- Python 3
- ffmpeg
- rsync
- openssh-server

## Directory layout
```
space-media-server/
├─ vision_players/
│  ├─ sample_vision_player/    # full sample (mock)
│  │  ├─ config/
│  │  │  └─ vision_config.json
│  │  ├─ source/
│  │  │  ├─ media/             # source videos/images
│  │  │  └─ playlists/         # server-side playlists
│  │  └─ output/
│  │     ├─ media/             # encoded media (rsync to player)
│  │     └─ playlists/         # output playlists (rsync to player)
│  └─ _template/
│     ├─ config/
│     └─ source/
│        ├─ media/
│        └─ playlists/
├─ system_media/
└─ tools/
   ├─ encode.py
   ├─ init_vision.py
   ├─ emit_sync_env.py
   ├─ push_media.sh
   └─ encode_and_push.sh
```

## Raspberry Pi 5 setup (recommended)
1) OS インストール
- Debian / Raspberry Pi OS
- リポジトリ配置: `/srv/space-media-server`

2) パッケージ導入
```
sudo apt-get update
sudo apt-get install -y \
  ffmpeg \
  rsync \
  openssh-server
```

3) WLAN AP (hostapd + dnsmasq)
- wlan0 を hotspot 化
- DHCP leases file: `/var/lib/misc/dnsmasq.leases`

4) RTC (必要なら)
- RTC モジュール接続
- `hwclock` が使えることを確認

5) SSH 鍵
- media-server → vision-player へ SSH 鍵を通す

6) 任意: Samba
- Windows から素材を置きたい場合のみ

## Configuration
### `vision_players/<vision_id>/config/vision_config.json`
- cabinet / screen / lanes を定義
- lane_policy / encoding 方針を設定

## Playlists (server-side)
- `vision_players/<vision_id>/source/playlists/<weekday>.json`
- items は `source` を使う
- `auto_policy` でフォルダから自動生成も可能

JSONC sample:
- `vision_players/_template/source/playlists/sample.jsonc`

## Encoding policy
- 画像は動画化（duration_sec）
- 動画は rect サイズに変換
- audio_normalize で音量統一可

## Usage
### Daily workflow (short)
1) 素材を置く
```
vision_players/<vision_id>/source/media/
```
2) playlist 編集
```
vision_players/<vision_id>/source/playlists/<weekday>.json
```
3) Encode
```
./tools/encode.py --vision-id <vision_id> --playlist ./vision_players/<vision_id>/source/playlists/<weekday>.json
```
4) Push (DHCP lease lookup)
```
PLAYER_HOSTNAME=<player-hostname> VISION_ID=<vision_id> ./tools/push_media.sh
```

### Encode (single / all)
```
./tools/encode.py --vision-id sample_vision_player --playlist ./vision_players/sample_vision_player/source/playlists/always.json
./tools/encode.py --vision-id sample_vision_player --all
```

Tip: add a shell alias if you forget the command:
```
alias encode-sample='cd /srv/space-media-server && ./tools/encode.py --vision-id sample_vision_player --all'
```

### One-shot (encode + push)
```
VISION_ID=sample_vision_player PLAYER_HOSTNAME=vision-player-akiba-01 ./tools/encode_and_push.sh
```

### How to run (copy/paste)
1) Encode only:
```
VISION_ID=sample_vision_player ./tools/encode.py --vision-id sample_vision_player --all
```
2) Push only:
```
PLAYER_HOSTNAME=vision-player-akiba-01 VISION_ID=sample_vision_player ./tools/push_media.sh
```
3) Encode + Push:
```
VISION_ID=sample_vision_player PLAYER_HOSTNAME=vision-player-akiba-01 ./tools/encode_and_push.sh
```

### Sync behavior (push)
- rsync 前に `state/media_updating.flag` を作成
- `output/media/` → `output/playlists/` の順に push
- 成功時に flag を削除（失敗時は残す）

環境変数（push）:
- `VISION_ID` (必須)
- `PLAYER_HOSTNAME` (任意)
- `PLAYER_USER` (任意)
- `REMOTE_BASE` (任意)
- `MEDIA_ROOT` (任意)
- `LEASES_FILE` (任意)
- `RSYNC_OPTS` (任意)
- `RSYNC_DELETE=1` で削除も反映（デフォルト有効）

## Notes
- `output/media` は生成物。古い動画を残さない場合は削除 or `RSYNC_DELETE=1` を使う。
- vision-player 側は Xorg が無いと mpv が全画面になり geometry が効かない。

## References
- playlist spec: `vision_players/_template/source/playlists/sample.jsonc`
