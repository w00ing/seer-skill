#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOT'
record_app_window.sh

Record a macOS app window as a .mov using screencapture.

Usage:
  record_app_window.sh [--out <video.mov>] [--process <app_name>] [--duration <sec>] [--frames] [--fps <n>] [--frames-dir <dir>] [--json]

Options:
  --out         Output video path (default: .seer/record/app-window-<app>-<ts>-<pid>-<rand>.mov)
  --process     App process name to capture (default: frontmost app)
  --duration    Recording duration in seconds (default: 3)
  --frames      Extract frames after recording
  --fps         Frames per second for extraction (default: 10)
  --frames-dir  Output dir for extracted frames (default: .seer/record/frames-<app>-<ts>-<pid>-<rand>)
  --json        Print JSON metadata to stdout

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)

Notes:
  - Requires Screen Recording permission for terminal.
  - Frame extraction requires ffmpeg.
EOT
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
record_dir="${out_root}/record"
ts=$(date +%Y%m%d-%H%M%S)
run_id="${ts}-$$-$RANDOM"

out=""
process=""
duration=3
extract_frames=0
fps=10
frames_dir=""
print_json=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      out="${2:-}"
      shift 2
      ;;
    --process)
      process="${2:-}"
      shift 2
      ;;
    --duration)
      duration="${2:-}"
      shift 2
      ;;
    --frames)
      extract_frames=1
      shift
      ;;
    --fps)
      fps="${2:-}"
      shift 2
      ;;
    --frames-dir)
      frames_dir="${2:-}"
      shift 2
      ;;
    --json)
      print_json=1
      shift
      ;;
    *)
      echo "error: unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${process}" ]]; then
  process=$(osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' 2>/dev/null || true)
fi

slug=$(echo "${process}" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9._-')
if [[ -z "${slug}" ]]; then
  slug="app"
fi

if [[ -z "${out}" ]]; then
  out="${record_dir}/app-window-${slug}-${run_id}.mov"
fi

if [[ -z "${frames_dir}" ]]; then
  frames_dir="${record_dir}/frames-${slug}-${run_id}"
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

screencapture -x -v -R "${x},${y},${w},${h}" -V "${duration}" "${out}"

frames_out=""
if [[ ${extract_frames} -eq 1 ]]; then
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "error: ffmpeg not found (required for frame extraction)"
    exit 1
  fi
  mkdir -p "${frames_dir}"
  ffmpeg -hide_banner -loglevel error -i "${out}" -vf "fps=${fps}" "${frames_dir}/frame-%04d.png"
  frames_out="${frames_dir}"
fi

if [[ ${print_json} -eq 1 ]]; then
  VIDEO_PATH="${out}" FRAMES_DIR="${frames_out}" APP_NAME="${process}" APP_SLUG="${slug}" DURATION="${duration}" FPS="${fps}" \
  python3 - <<'PY'
import json
import os

payload = {
    "app_name": os.environ.get("APP_NAME") or None,
    "app_slug": os.environ.get("APP_SLUG") or None,
    "duration": float(os.environ.get("DURATION") or 0),
    "fps": float(os.environ.get("FPS") or 0),
    "video_path": os.path.abspath(os.environ.get("VIDEO_PATH") or ""),
    "frames_dir": os.path.abspath(os.environ.get("FRAMES_DIR") or "") if os.environ.get("FRAMES_DIR") else None,
}
print(json.dumps(payload))
PY
else
  echo "${out}"
  if [[ -n "${frames_out}" ]]; then
    echo "${frames_out}"
  fi
fi
