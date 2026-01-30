#!/usr/bin/env bash
set -euo pipefail

# One-shot: encode then push to vision-player.
# Usage:
#   VISION_ID=akiba_01 PLAYER_HOSTNAME=vision-player-akiba-01 ./bin/encode_and_push.sh
# Optional:
#   PLAYLIST=./vision_players/<vision_id>/source/playlists/<weekday>.json
#   PLAYER_USER=pi
#   REPO_ROOT=/srv/space-media-server
#   LEASES_FILE=/var/lib/misc/dnsmasq.leases
#   PLAYER_IP=192.168.1.1
#   RSYNC_OPTS="-az --size-only"
#   RSYNC_DELETE=1
#   CLEAN_OUTPUT=1   # default: 1 (remove output/media & output/playlists before encode)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VISION_ID="${VISION_ID:-}"

if [[ -z "${VISION_ID}" ]]; then
  if [[ -t 0 ]]; then
    echo "Select VISION_ID:" >&2
    mapfile -t VISION_DIRS < <(find "${REPO_ROOT}/vision_players" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort)
    if [[ "${#VISION_DIRS[@]}" -eq 0 ]]; then
      echo "No vision_players found. Run: ./bin/init_vision.py <vision_id>" >&2
      exit 1
    fi
    for i in "${!VISION_DIRS[@]}"; do
      printf "  [%d] %s\n" "$((i+1))" "${VISION_DIRS[$i]}" >&2
    done
    read -r -p "Choose number: " choice
    if ! [[ "${choice}" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#VISION_DIRS[@]} )); then
      echo "Invalid choice." >&2
      exit 1
    fi
    VISION_ID="${VISION_DIRS[$((choice-1))]}"
  else
    echo "Set VISION_ID (ex: akiba_01)" >&2
    exit 1
  fi
fi

PLAYER_HOSTNAME="${PLAYER_HOSTNAME:-${VISION_ID}}"
PLAYER_USER="${PLAYER_USER:-${USER:-pi}}"
PLAYER_IP="${PLAYER_IP:-}"
# Backward compat: MEDIA_ROOT -> REPO_ROOT
REPO_ROOT="${REPO_ROOT:-${MEDIA_ROOT:-${REPO_ROOT}}}"
LEASES_FILE="${LEASES_FILE:-/var/lib/misc/dnsmasq.leases}"
PLAYLIST="${PLAYLIST:-}"
CLEAN_OUTPUT="${CLEAN_OUTPUT:-1}"

if [[ "${CLEAN_OUTPUT}" == "1" && -n "${VISION_ID}" ]]; then
  OUT_DIR="${REPO_ROOT}/vision_players/${VISION_ID}/output"
  rm -rf "${OUT_DIR}/media" "${OUT_DIR}/playlists"
fi

if [[ -n "${VISION_ID}" ]]; then
  if [[ -n "${PLAYLIST}" ]]; then
    echo "[encode_and_push] encode start (vision_id=${VISION_ID}, playlist=${PLAYLIST})"
    ./bin/encode.py --vision-id "${VISION_ID}" --playlist "${PLAYLIST}"
  else
    echo "[encode_and_push] encode start (vision_id=${VISION_ID}, playlist=interactive/all)"
    ./bin/encode.py --vision-id "${VISION_ID}"
  fi
else
  if [[ -n "${PLAYLIST}" ]]; then
    echo "[encode_and_push] encode start (playlist=${PLAYLIST})"
    ./bin/encode.py --playlist "${PLAYLIST}"
  else
    echo "[encode_and_push] encode start (interactive/all)"
    ./bin/encode.py
  fi
fi

echo "[encode_and_push] push start"
PLAYER_HOSTNAME="${PLAYER_HOSTNAME}" \
PLAYER_USER="${PLAYER_USER}" \
PLAYER_IP="${PLAYER_IP}" \
REPO_ROOT="${REPO_ROOT}" \
LEASES_FILE="${LEASES_FILE}" \
RSYNC_OPTS="${RSYNC_OPTS:-}" \
RSYNC_DELETE="${RSYNC_DELETE:-1}" \
VISION_ID="${VISION_ID}" \
./bin/push_media.sh
echo "[encode_and_push] done"
