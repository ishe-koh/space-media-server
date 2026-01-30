#!/usr/bin/env bash
set -euo pipefail

# Push output/ to vision-player discovered via DHCP lease (dnsmasq).
# Usage:
#   VISION_ID=akiba_01 ./bin/push_media.sh
# Optional env:
#   PLAYER_HOSTNAME=akiba_01
#   PLAYER_IP=192.168.1.1
#   PLAYER_USER=pi
#   REMOTE_PLAYER_ROOT=/home/pi/space-vision-player
#   REMOTE_OUTPUT_DIR=/home/pi/space-vision-player/vision_players/<vision_id>/output
#   REPO_ROOT=/srv/space-media-server
#   LEASES_FILE=/var/lib/misc/dnsmasq.leases
#   RSYNC_OPTS="-az --size-only"
#   RSYNC_DELETE=1   # default: 1 (delete removed files)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VISION_ID="${VISION_ID:-}"
if [[ -z "${VISION_ID}" ]]; then
  echo "Set VISION_ID (ex: akiba_01)" >&2
  exit 1
fi

PLAYER_HOSTNAME="${PLAYER_HOSTNAME:-vision-player-${VISION_ID}}"
PLAYER_HOSTNAME="${PLAYER_HOSTNAME//_/-}"
PLAYER_IP="${PLAYER_IP:-}"
PLAYER_USER="${PLAYER_USER:-${USER:-pi}}"
# Backward compat: MEDIA_ROOT -> REPO_ROOT
REPO_ROOT="${REPO_ROOT:-${MEDIA_ROOT:-${REPO_ROOT}}}"
LEASES_FILE="${LEASES_FILE:-/var/lib/misc/dnsmasq.leases}"
REMOTE_PLAYER_ROOT="${REMOTE_PLAYER_ROOT:-/home/${PLAYER_USER}/space-vision-player}"
REMOTE_OUTPUT_DIR="${REMOTE_OUTPUT_DIR:-${REMOTE_PLAYER_ROOT}/vision_players/${VISION_ID}/output}"

VISION_DIR="${REPO_ROOT}/vision_players/${VISION_ID}"
if [[ ! -d "${VISION_DIR}" ]]; then
  echo "vision dir not found: ${VISION_DIR}" >&2
  exit 1
fi

OUTPUT_MEDIA_DIR="${VISION_DIR}/output/media"
OUTPUT_PLAYLISTS_DIR="${VISION_DIR}/output/playlists"
if [[ ! -d "${OUTPUT_MEDIA_DIR}" ]]; then
  echo "output media dir not found: ${OUTPUT_MEDIA_DIR}" >&2
  exit 1
fi
if [[ ! -d "${OUTPUT_PLAYLISTS_DIR}" ]]; then
  echo "output playlists dir not found: ${OUTPUT_PLAYLISTS_DIR}" >&2
  exit 1
fi

if [[ ! -f "${LEASES_FILE}" ]]; then
  echo "leases file not found: ${LEASES_FILE}" >&2
  exit 1
fi

# dnsmasq leases format: expiry mac ip hostname clientid
if [[ -z "${PLAYER_IP}" ]]; then
  PLAYER_IP="$(awk -v h="${PLAYER_HOSTNAME}" '$4 == h {print $3}' "${LEASES_FILE}" | tail -n 1)"
  if [[ -z "${PLAYER_IP}" && "${PLAYER_HOSTNAME}" != "${VISION_ID}" ]]; then
    PLAYER_IP="$(awk -v h="${VISION_ID}" '$4 == h {print $3}' "${LEASES_FILE}" | tail -n 1)"
    if [[ -n "${PLAYER_IP}" ]]; then
      PLAYER_HOSTNAME="${VISION_ID}"
    fi
  fi
  if [[ -z "${PLAYER_IP}" ]]; then
    echo "[push] waiting for hostname in leases (timeout 15s)..." >&2
    found_ip=""
    for _ in {1..15}; do
      found_ip="$(awk -v h="${PLAYER_HOSTNAME}" '$4 == h {print $3}' "${LEASES_FILE}" | tail -n 1)"
      if [[ -n "${found_ip}" ]]; then
        PLAYER_IP="${found_ip}"
        break
      fi
      sleep 1
    done
    if [[ -z "${PLAYER_IP}" ]]; then
      echo "hostname not found in leases: ${PLAYER_HOSTNAME}" >&2
    fi
  fi
  if [[ -z "${PLAYER_IP}" ]]; then
    if [[ -t 0 ]]; then
      echo "Select a host from leases:" >&2
      mapfile -t HOSTS < <(awk '{print $4}' "${LEASES_FILE}" | sort -u)
      if [[ "${#HOSTS[@]}" -eq 0 ]]; then
        echo "No hosts found in leases. Set PLAYER_IP to use a fixed address." >&2
        exit 1
      fi
      for i in "${!HOSTS[@]}"; do
        printf "  [%d] %s\n" "$((i+1))" "${HOSTS[$i]}" >&2
      done
      read -r -p "Choose host number: " choice
      if ! [[ "${choice}" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#HOSTS[@]} )); then
        echo "Invalid choice." >&2
        exit 1
      fi
      PLAYER_HOSTNAME="${HOSTS[$((choice-1))]}"
      PLAYER_IP="$(awk -v h="${PLAYER_HOSTNAME}" '$4 == h {print $3}' "${LEASES_FILE}" | tail -n 1)"
    else
      echo "Set PLAYER_IP to use a fixed address." >&2
      exit 1
    fi
  fi
fi

RSYNC_OPTS_DEFAULT="-az --size-only"
RSYNC_OPTS="${RSYNC_OPTS:-${RSYNC_OPTS_DEFAULT}}"
RSYNC_DELETE="${RSYNC_DELETE:-1}"
if [[ "${RSYNC_DELETE}" == "1" ]]; then
  RSYNC_OPTS="${RSYNC_OPTS} --delete"
fi

SSH_IDENTITY="${SSH_IDENTITY:-}"
SSH_OPTS="${SSH_OPTS:-}"
if [[ -n "${SSH_IDENTITY}" ]]; then
  SSH_OPTS="-i ${SSH_IDENTITY} ${SSH_OPTS}"
fi
SSH_OPTS="-o BatchMode=yes ${SSH_OPTS}"
SSH_CMD="ssh ${SSH_OPTS}"
RSYNC_SSH="ssh ${SSH_OPTS}"

REMOTE_HOST="${PLAYER_USER}@${PLAYER_IP}"
REMOTE_STATE_DIR="${REMOTE_PLAYER_ROOT}/state"
REMOTE_FLAG_PATH="${REMOTE_STATE_DIR}/media_updating.flag"
REMOTE_MEDIA_DIR="${REMOTE_OUTPUT_DIR}/media"
REMOTE_PLAYLISTS_DIR="${REMOTE_OUTPUT_DIR}/playlists"

echo "[push] target ${PLAYER_USER}@${PLAYER_IP} (${PLAYER_HOSTNAME})"

echo "[push] set updating flag..."
${SSH_CMD} "${REMOTE_HOST}" "mkdir -p '${REMOTE_STATE_DIR}' && touch '${REMOTE_FLAG_PATH}'"
cleanup_flag=1
cleanup() {
  if [[ "${cleanup_flag}" == "1" ]]; then
    ${SSH_CMD} "${REMOTE_HOST}" "rm -f '${REMOTE_FLAG_PATH}'" || true
  fi
}
trap cleanup EXIT

echo "[push] syncing output/media..."
${SSH_CMD} "${REMOTE_HOST}" "mkdir -p '${REMOTE_MEDIA_DIR}'"
rsync ${RSYNC_OPTS} -e "${RSYNC_SSH}" \
  "${OUTPUT_MEDIA_DIR}/" "${REMOTE_HOST}:${REMOTE_MEDIA_DIR}/"

echo "[push] syncing output/playlists..."
${SSH_CMD} "${REMOTE_HOST}" "mkdir -p '${REMOTE_PLAYLISTS_DIR}'"
rsync ${RSYNC_OPTS} -e "${RSYNC_SSH}" \
  "${OUTPUT_PLAYLISTS_DIR}/" "${REMOTE_HOST}:${REMOTE_PLAYLISTS_DIR}/"

echo "[push] restart space-vision-player..."
${SSH_CMD} "${REMOTE_HOST}" "sudo -n systemctl restart space-vision-player"

echo "[push] clear updating flag..."
${SSH_CMD} "${REMOTE_HOST}" "rm -f '${REMOTE_FLAG_PATH}'"
cleanup_flag=0
