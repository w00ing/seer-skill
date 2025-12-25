#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
capture_app_window.sh

Usage:
  capture_app_window.sh [out_path] [process_name]

Defaults:
  out_path     .seer/captures/app-window-<app>-YYYYMMDD-HHMMSS-<pid>-<rand>.png
  process_name frontmost app

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
ts=$(date +%Y%m%d-%H%M%S)
out=${1:-}
process=${2:-}

if [[ -z "${process}" ]]; then
  process=$(osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' 2>/dev/null || true)
fi

slug=$(echo "${process}" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9._-')
if [[ -z "${slug}" ]]; then
  slug="app"
fi

if [[ -z "${out}" ]]; then
  out="${out_root}/captures/app-window-${slug}-${ts}-$$-$RANDOM.png"
fi

pos=$(osascript -e "tell application \"System Events\" to tell process \"${process}\" to get position of window 1" 2>/dev/null || true)
size=$(osascript -e "tell application \"System Events\" to tell process \"${process}\" to get size of window 1" 2>/dev/null || true)

if [[ -z "${pos}" || -z "${size}" ]]; then
  echo "error: window not found for process '${process}'"
  echo "hint: verify app is running, Accessibility enabled for terminal, and process name (try exact app name)"
  exit 1
fi

pos=$(echo "${pos}" | tr -d ' ')
size=$(echo "${size}" | tr -d ' ')

x=${pos%,*}
y=${pos#*,}
w=${size%,*}
h=${size#*,}

mkdir -p "$(dirname "${out}")"
screencapture -x -R "${x},${y},${w},${h}" "${out}"

echo "${out}"
