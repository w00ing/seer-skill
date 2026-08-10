#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
capture_app_window.sh

Usage:
  capture_app_window.sh [out_path] [process_name]

Defaults:
  out_path     .seer/capture/app-window-<app>-YYYYMMDD-HHMMSS-<pid>-<rand>.png
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
if [[ "${1:-}" == "--" ]]; then
  shift
fi

for command in osascript screencapture; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "error: required command not found: ${command}" >&2
    exit 2
  fi
done

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
captures_dir="${out_root}/capture"
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
  out="${captures_dir}/app-window-${slug}-${ts}-$$-$RANDOM.png"
fi

pos=$(osascript - "${process}" 2>/dev/null <<'APPLESCRIPT' || true
on run argv
  tell application "System Events" to tell process (item 1 of argv) to get position of window 1
end run
APPLESCRIPT
)
size=$(osascript - "${process}" 2>/dev/null <<'APPLESCRIPT' || true
on run argv
  tell application "System Events" to tell process (item 1 of argv) to get size of window 1
end run
APPLESCRIPT
)

if [[ -z "${pos}" || -z "${size}" ]]; then
  echo "error: window not found for process '${process}'" >&2
  echo "hint: verify app is running, Accessibility enabled for terminal, and process name (try exact app name)" >&2
  exit 2
fi

pos=$(echo "${pos}" | tr -d ' ')
size=$(echo "${size}" | tr -d ' ')

x=${pos%,*}
y=${pos#*,}
w=${size%,*}
h=${size#*,}

mkdir -p "$(dirname "${out}")"
if ! screencapture -x -R "${x},${y},${w},${h}" "${out}"; then
  echo "error: screen capture failed; verify Screen Recording permission" >&2
  exit 2
fi

echo "${out}"
