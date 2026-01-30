#!/usr/bin/env bash
set -euo pipefail

# One-shot: encode then push to vision-player.
# Usage:
#   VISION_ID=akiba_01 PLAYER_HOSTNAME=vision-player-akiba-01 ./tools/encode_and_push.sh
# Optional:
#   PLAYLIST=./media/<vision_id>/source/playlists/<weekday>.json
#   PLAYER_USER=ishii
#   MEDIA_ROOT=/srv/space-media-server
#   LEASES_FILE=/var/lib/misc/dnsmasq.leases
#   RSYNC_OPTS="-az --size-only"
#   RSYNC_DELETE=1
#   CLEAN_OUTPUT=1   # default: 1 (remove out/encoded & out/playlists before encode)

VISION_ID="${VISION_ID:-}"
if [[ -z "${VISION_ID}" ]]; then
  echo "Set VISION_ID (ex: akiba_01)" >&2
  exit 1
fi

PLAYER_HOSTNAME="${PLAYER_HOSTNAME:-${VISION_ID}}"
PLAYER_USER="${PLAYER_USER:-ishii}"
MEDIA_ROOT="${MEDIA_ROOT:-$(pwd)}"
LEASES_FILE="${LEASES_FILE:-/var/lib/misc/dnsmasq.leases}"
PLAYLIST="${PLAYLIST:-${MEDIA_ROOT}/vision_players/${VISION_ID}/source/playlists/always.json}"
CLEAN_OUTPUT="${CLEAN_OUTPUT:-1}"

if [[ "${CLEAN_OUTPUT}" == "1" ]]; then
  OUT_DIR="${MEDIA_ROOT}/vision_players/${VISION_ID}/output"
  rm -rf "${OUT_DIR}/media" "${OUT_DIR}/playlists"
fi

./tools/encode.py --vision-id "${VISION_ID}" --playlist "${PLAYLIST}"

PLAYER_HOSTNAME="${PLAYER_HOSTNAME}" \
PLAYER_USER="${PLAYER_USER}" \
MEDIA_ROOT="${MEDIA_ROOT}" \
LEASES_FILE="${LEASES_FILE}" \
RSYNC_OPTS="${RSYNC_OPTS:-}" \
RSYNC_DELETE="${RSYNC_DELETE:-0}" \
VISION_ID="${VISION_ID}" \
./tools/push_media.sh
