#!/usr/bin/env bash
set -euo pipefail

# Push encoded outputs to vision-player discovered via DHCP lease (dnsmasq).
# Usage:
#   VISION_ID=akiba_01 ./tools/push_media.sh
# Optional env:
#   PLAYER_HOSTNAME=akiba_01
#   PLAYER_USER=ishii
#   REMOTE_BASE=/home/ishii/space-vision-player
#   MEDIA_ROOT=/srv/space-media-server
#   LEASES_FILE=/var/lib/misc/dnsmasq.leases
#   RSYNC_OPTS="-az --size-only"
#   RSYNC_DELETE=1   # default: 1 (delete removed files)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VISION_ID="${VISION_ID:-}"
if [[ -z "${VISION_ID}" ]]; then
  echo "Set VISION_ID (ex: akiba_01)" >&2
  exit 1
fi

PLAYER_HOSTNAME="${PLAYER_HOSTNAME:-${VISION_ID}}"
PLAYER_USER="${PLAYER_USER:-ishii}"
MEDIA_ROOT="${MEDIA_ROOT:-${REPO_ROOT}}"
LEASES_FILE="${LEASES_FILE:-/var/lib/misc/dnsmasq.leases}"
REMOTE_BASE="${REMOTE_BASE:-/home/${PLAYER_USER}/space-vision-player}"

OUT_DIR="${MEDIA_ROOT}/vision_players/${VISION_ID}/output"
if [[ ! -d "${OUT_DIR}" ]]; then
  echo "out dir not found: ${OUT_DIR}" >&2
  exit 1
fi

if [[ ! -f "${LEASES_FILE}" ]]; then
  echo "leases file not found: ${LEASES_FILE}" >&2
  exit 1
fi

# dnsmasq leases format: expiry mac ip hostname clientid
PLAYER_IP="$(awk -v h="${PLAYER_HOSTNAME}" '$4 == h {print $3}' "${LEASES_FILE}" | tail -n 1)"
if [[ -z "${PLAYER_IP}" ]]; then
  echo "hostname not found in leases: ${PLAYER_HOSTNAME}" >&2
  exit 1
fi

RSYNC_OPTS_DEFAULT="-az --size-only"
RSYNC_OPTS="${RSYNC_OPTS:-${RSYNC_OPTS_DEFAULT}}"
RSYNC_DELETE="${RSYNC_DELETE:-1}"
if [[ "${RSYNC_DELETE}" == "1" ]]; then
  RSYNC_OPTS="${RSYNC_OPTS} --delete"
fi

REMOTE_HOST="${PLAYER_USER}@${PLAYER_IP}"
REMOTE_STATE_DIR="${REMOTE_BASE}/state"
REMOTE_FLAG_PATH="${REMOTE_STATE_DIR}/media_updating.flag"

echo "[push] target ${PLAYER_USER}@${PLAYER_IP} (${PLAYER_HOSTNAME})"

echo "[push] set updating flag..."
ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_STATE_DIR}' && touch '${REMOTE_FLAG_PATH}'"

echo "[push] syncing encoded..."
rsync ${RSYNC_OPTS} "${OUT_DIR}/media/" "${REMOTE_HOST}:${REMOTE_BASE}/encoded/"

echo "[push] syncing playlists..."
rsync ${RSYNC_OPTS} "${OUT_DIR}/playlists/" "${REMOTE_HOST}:${REMOTE_BASE}/playlists/"

echo "[push] clear updating flag..."
ssh "${REMOTE_HOST}" "rm -f '${REMOTE_FLAG_PATH}'"
