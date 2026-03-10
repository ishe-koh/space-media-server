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
- smbd

## Directory layout
```
space-media-server/
├─ vision_players/
│  └─ sample_vision_player/    # full sample (mock)
│     ├─ config/
│     │  └─ vision_config.json
│     ├─ source/
│     │  ├─ media/             # source videos/images
│     │  │  └─ is_limited/     # manually referenced media with availability window
│     │  └─ playlists/         # server-side playlists
│     │     ├─ always.json
│     │     ├─ mon.json ... sun.json
│     │     └─ sample.jsonc    # JSONC format guide
│     ├─ trigger/              # drop RUN here to trigger encode+push
│     └─ output/
│        ├─ media/             # output media (rsync to player)
│        └─ playlists/         # output playlists (rsync to player)
├─ app/                        # python package (core logic)
└─ bin/                        # CLI / scripts
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
  openssh-server \
  inotify-tools
```

3) WLAN AP (hostapd + dnsmasq)
- wlan0 を hotspot 化
- DHCP leases file: `/var/lib/misc/dnsmasq.leases`

4) RTC (必要なら)
- RTC モジュール接続
- `hwclock` が使えることを確認

5) SSH 鍵
- media-server → vision-player へ SSH 鍵を通す（パスフレーズ無し推奨）
- known_hosts に登録してパスワード確認を回避:
```
ssh-keyscan -H {PLAYER_HOSTNAME} >> ~/.ssh/known_hosts
```
- できれば専用ユーザーを作成して、共有フォルダ以下だけ書き込み可能にする
- さらに厳密にするなら authorized_keys を rsync 専用に固定（任意）
  - 例: rrsync を使う場合（環境に rrsync があるとき）
```
command="/usr/bin/rrsync -rw /home/pi/space-vision-player/vision_players",no-port-forwarding,no-pty,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA...
```

6) 任意: Samba
- Windows から素材を置きたい場合のみ

## Configuration
### `vision_players/<vision_id>/config/vision_config.json`
- cabinet / screen / lanes を定義
- lane_policy / encoding 方針を設定

## Playlists (server-side)
- `vision_players/<vision_id>/source/playlists/<weekday>.json`
- items は `source` を使う（`source/` からの相対パス。例: `media/tue/foo.mp4`）
- `auto_policy` でフォルダから自動生成も可能
- 期間限定素材は `source/media/is_limited/` に置き、item object の
  `is_available_from` / `is_available_until` で配信期間を指定する運用を推奨

JSONC guide:
- `vision_players/sample_vision_player/source/playlists/sample.jsonc`

## Encoding policy
- 画像は動画化（duration_sec）
- 動画は rect サイズに変換
- audio_normalize で音量統一可

## Usage
### Playlist wizard (recommended)
Interactive generator to avoid JSON syntax errors:
```
./bin/gen_playlist.py
```
This writes:
```
vision_players/<vision_id>/source/playlists/<weekday>.json
```

### Web UI (nginx reverse proxy)
1) Install nginx
```
sudo apt-get install -y nginx
```
2) Place config and enable
```
sudo cp systemd/nginx-space-media-server.conf /etc/nginx/sites-available/space-media-server
sudo ln -sf /etc/nginx/sites-available/space-media-server /etc/nginx/sites-enabled/space-media-server
sudo nginx -t
sudo systemctl reload nginx
```
Note: large video uploads may require raising `client_max_body_size` in the nginx config.
3) Ensure web UI listens on localhost (default in systemd unit)
```
sudo systemctl restart space-media-server-webui
```
The UI can also upload media into:
```
vision_players/<vision_id>/source/media/<weekday>/
```
Environment:
- `WEB_UI_HOST` (default: 127.0.0.1)
- `WEB_UI_PORT` (default: 8080)
Known targets:
- `config/known_targets.json` (optional)
  - Example:
    ```json
    [{"name":"vision-player-akiba-01","target":"vision-player-akiba-01","ip":"192.168.210.58"}]
    ```

### Web UI (systemd)
```
sudo cp systemd/space-media-server-webui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now space-media-server-webui
```
Important: use a single OS user for Web UI + repo + encode output ownership.
- If you run Web UI as `deploy`, keep `/srv/space-media-server` and `vision_players/*/output` owned by `deploy`.
- If you run Web UI as `ishii`, set the systemd user to `ishii` and keep the repo/output owned by `ishii`.
- The SSH key used by Web UI should be **passphrase-less** (BatchMode SSH).

### Deploy user (recommended)
To avoid per-user SSH key issues, run Web UI as a dedicated `deploy` user.

On media-server:
```
sudo useradd -m -s /bin/bash deploy
sudo chown -R deploy:deploy /srv/space-media-server
sudo -u deploy ssh-keygen -t ed25519 -a 100 -f /home/deploy/.ssh/id_ed25519
```

On vision-player:
```
sudo useradd -m -s /bin/bash deploy
sudo passwd deploy
# DEFINE PASSWORD
sudo mkdir -p /home/deploy/.ssh
# copy the media-server deploy public key into vision-player
# (run this on media-server)
sudo cat /home/deploy/.ssh/id_ed25519.pub | ssh deploy@<VISION_PLAYER_HOST> "mkdir -p /home/deploy/.ssh && cat >> /home/deploy/.ssh/authorized_keys"
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

If you want a different user, edit the systemd unit:
```
sudo systemctl edit space-media-server-webui
```
and set:
```
[Service]
User=<your-user>
Group=<your-user>
```

## Architecture (flow)
High-level flow:
```
bin/encode.py
  -> app/encoding_pipeline.py
     -> app/config_loader.py (load vision_config.json)
     -> app/layout_calc.py (compute lane rects)
     -> ffmpeg (encode media)
  -> writes output/media + output/playlists

bin/push_media.sh
  -> ssh (touch/remove state/media_updating.flag)
  -> rsync output/media + output/playlists to vision-player
  -> rsync uses --delete by default (stale files removed)

bin/encode_and_push.sh
  -> bin/encode.py
  -> bin/push_media.sh
```

### Daily workflow (short)
1) 素材を置く
```
vision_players/<vision_id>/source/media/
```
曜日フォルダで運用する場合:
```
vision_players/<vision_id>/source/media/mon
vision_players/<vision_id>/source/media/tue
...
```
2) playlist 編集
```
vision_players/<vision_id>/source/playlists/<weekday>.json
```
3) Encode
```
./bin/encode.py --vision-id <vision_id> --playlist ./vision_players/<vision_id>/source/playlists/<weekday>.json
```
4) Push (DHCP lease lookup)
```
PLAYER_HOSTNAME=<player-hostname> VISION_ID=<vision_id> ./bin/push_media.sh
```
Hostname が見つからない場合は IP を指定:
```
PLAYER_IP={IP_ADDRESS} PLAYER_USER=pi VISION_ID=<vision_id> ./bin/push_media.sh
```

### Encode (single / all)
```
./bin/encode.py --vision-id sample_vision_player --playlist ./vision_players/sample_vision_player/source/playlists/always.json
./bin/encode.py --vision-id sample_vision_player --all
```

Tip: add a shell alias if you forget the command:
```
alias encode-sample='cd /srv/space-media-server && ./bin/encode.py --vision-id sample_vision_player --all'
```

### One-shot (encode + push)
```
VISION_ID=sample_vision_player PLAYER_HOSTNAME={PLAYER_HOSTNAME} ./bin/encode_and_push.sh
```

### Trigger-based encode + push (SMB-friendly)
If you want "drop a file to trigger" (no auto-watch of large media uploads),
use a dedicated trigger directory and create a small flag file as the last step.

1) Start watcher:
```
VISION_ID=sample_vision_player ./bin/watch_encode_and_push.sh
```

2) From Windows/SMB, drop a file named `RUN` into:
```
vision_players/sample_vision_player/trigger/
```

Notes:
- The watcher listens only to the trigger dir.
- `RUN` is removed after a successful encode+push.
- Install `inotify-tools` for efficient watching:
```
sudo apt-get install -y inotify-tools
```

Env options:
- `TRIGGER_DIR` (default: `vision_players/<id>/trigger`)
- `TRIGGER_FILE` (default: `RUN`, set empty to trigger on any change)
- `DELETE_TRIGGER=1` (delete `RUN` on success)
- `ENCODE_AND_PUSH` (default: `./bin/encode_and_push.sh`)

### How to run (copy/paste)
1) Encode only:
```
VISION_ID=sample_vision_player ./bin/encode.py --vision-id sample_vision_player --all
```
2) Push only:
```
PLAYER_HOSTNAME={PLAYER_HOSTNAME} VISION_ID=sample_vision_player ./bin/push_media.sh
```
3) Encode + Push:
```
VISION_ID=sample_vision_player PLAYER_HOSTNAME={PLAYER_HOSTNAME} ./bin/encode_and_push.sh
```
`VISION_ID` / `PLAYLIST` を省略した場合は対話で選択される。

### Sync behavior (push)
- rsync 前に `state/media_updating.flag` を作成
- `output/media` → player の `vision_players/<id>/output/media`
- `output/playlists` → player の `vision_players/<id>/output/playlists`
- 成功時に flag を削除（失敗時は残す）

環境変数（push）:
- `VISION_ID` (必須)
- `PLAYER_HOSTNAME` (任意)
- `PLAYER_IP` (任意・hostname 不在時のフォールバック)
- `PLAYER_USER` (任意)
- `REMOTE_PLAYER_ROOT` (任意・既定は `/home/${PLAYER_USER}/space-vision-player`)
- `REMOTE_OUTPUT_DIR` (任意・既定は `/home/${PLAYER_USER}/space-vision-player/vision_players/${VISION_ID}/output`)
- `REPO_ROOT` (任意・既定は repo 直下)
- `LEASES_FILE` (任意)
- `RSYNC_OPTS` (任意)
- `RSYNC_DELETE=1` で削除も反映（デフォルト有効）

## Notes
- `output/media` は生成物。古い動画を残さない場合は削除 or `RSYNC_DELETE=1` を使う。
- vision-player 側は Xorg が無いと mpv が全画面になり geometry が効かない。

## References
- playlist spec: `vision_players/sample_vision_player/source/playlists/sample.jsonc`
