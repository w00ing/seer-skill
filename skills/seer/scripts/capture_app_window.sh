#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
capture_app_window.sh

Usage:
  capture_app_window.sh [out_path] [process_name]
  capture_app_window.sh --window-id ID [out_path]

Defaults:
  out_path     .seer/capture/app-window-<app>-YYYYMMDD-HHMMSS-<pid>-<rand>.png
  process_name frontmost app

Options:
  --window-id  Capture the exact session-scoped CGWindowID returned by `seer windows`

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)
EOF
}

window_id=
while [[ $# -gt 0 ]]; do
  case "${1}" in
    -h|--help)
      usage
      exit 0
      ;;
    --window-id)
      if [[ $# -lt 2 ]]; then
        echo "error: --window-id requires an ID" >&2
        exit 2
      fi
      window_id=${2}
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ -n "${window_id}" ]]; then
  if [[ ! "${window_id}" =~ ^[1-9][0-9]*$ ]] || [[ ${#window_id} -gt 10 ]] || (( 10#${window_id} > 4294967295 )); then
    echo "error: window ID must be between 1 and 4294967295" >&2
    exit 2
  fi
  window_id=$((10#${window_id}))
fi

if ! command -v screencapture >/dev/null 2>&1; then
  echo "error: required command not found: screencapture" >&2
  exit 2
fi
if [[ -z "${window_id}" ]] && ! command -v osascript >/dev/null 2>&1; then
  echo "error: required command not found: osascript" >&2
  exit 2
fi

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
captures_dir="${out_root}/capture"
ts=$(date +%Y%m%d-%H%M%S)
out=${1:-}
process=${2:-}

if [[ -n "${window_id}" && -n "${process}" ]]; then
  echo "error: --window-id cannot be combined with process_name" >&2
  exit 2
fi

if [[ -z "${window_id}" && -z "${process}" ]]; then
  process=$(osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' 2>/dev/null || true)
fi

slug=$(echo "${process:-window-${window_id}}" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9._-')
if [[ -z "${slug}" ]]; then
  slug="app"
fi

if [[ -z "${out}" ]]; then
  out="${captures_dir}/app-window-${slug}-${ts}-$$-$RANDOM.png"
fi

if [[ -n "${window_id}" ]]; then
  mkdir -p "$(dirname "${out}")"
  if ! screencapture -x -o -a "-l${window_id}" "${out}"; then
    echo "error: window ID ${window_id} is unavailable or screen capture permission is missing" >&2
    echo "hint: rerun 'seer windows --json'; IDs expire when a window closes or is recreated" >&2
    exit 2
  fi
  echo "${out}"
  exit 0
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
