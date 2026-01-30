#!/usr/bin/env bash
set -euo pipefail

VISION_ID="${VISION_ID:-}"
if [[ -z "$VISION_ID" ]]; then
  echo "VISION_ID is required" >&2
  exit 1
fi

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
TRIGGER_DIR="${TRIGGER_DIR:-$REPO_ROOT/vision_players/$VISION_ID/trigger}"
TRIGGER_FILE="${TRIGGER_FILE:-RUN}"
COOLDOWN_SEC="${COOLDOWN_SEC:-5}"
DELETE_TRIGGER="${DELETE_TRIGGER:-1}"
ENCODE_AND_PUSH="${ENCODE_AND_PUSH:-$REPO_ROOT/bin/encode_and_push.sh}"

mkdir -p "$TRIGGER_DIR"

lock_or_skip() {
  local lock_dir="$TRIGGER_DIR/.lock"
  if mkdir "$lock_dir" 2>/dev/null; then
    trap 'rmdir "$lock_dir"' RETURN
    return 0
  fi
  return 1
}

should_run() {
  if [[ -n "$TRIGGER_FILE" ]]; then
    [[ -f "$TRIGGER_DIR/$TRIGGER_FILE" ]]
  else
    return 0
  fi
}

run_once() {
  if ! should_run; then
    return 0
  fi
  if ! lock_or_skip; then
    return 0
  fi
  if "$ENCODE_AND_PUSH"; then
    if [[ "$DELETE_TRIGGER" == "1" && -n "$TRIGGER_FILE" ]]; then
      rm -f "$TRIGGER_DIR/$TRIGGER_FILE"
    fi
  else
    echo "[watch] encode_and_push failed; leaving trigger as-is" >&2
    return 1
  fi
}

if command -v inotifywait >/dev/null 2>&1; then
  while true; do
    inotifywait -e close_write,move,create "$TRIGGER_DIR" >/dev/null
    run_once || true
  done
else
  last_mtime=""
  while true; do
    current_mtime="$(stat -c %Y "$TRIGGER_DIR" 2>/dev/null || echo 0)"
    if [[ "$current_mtime" != "$last_mtime" ]]; then
      last_mtime="$current_mtime"
      run_once || true
    fi
    sleep "$COOLDOWN_SEC"
  done
fi
