#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOT'
record_screen.sh

Record the screen (or a region) as a .mov using screencapture.

Usage:
  record_screen.sh [--out <video.mov>] [--duration <sec>] [--display <n>] [--region <x,y,w,h>] [--json]

Options:
  --out       Output video path (default: .seer/record/screen-<ts>-<pid>-<rand>.mov)
  --duration  Recording duration in seconds (default: 3)
  --display   Display index for full-screen capture (uses screencapture -D)
  --region    Capture a rect as "x,y,w,h" (uses screencapture -R)
  --json      Print JSON metadata to stdout

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)

Notes:
  - Requires Screen Recording permission for terminal.
  - If both --display and --region are provided, --region wins.
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
duration=3
display=""
region=""
print_json=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      out="${2:-}"
      shift 2
      ;;
    --duration)
      duration="${2:-}"
      shift 2
      ;;
    --display)
      display="${2:-}"
      shift 2
      ;;
    --region)
      region="${2:-}"
      shift 2
      ;;
    --json)
      print_json=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${out}" ]]; then
  out="${record_dir}/screen-${run_id}.mov"
fi

mkdir -p "$(dirname "${out}")"

args=( -x -v -V "${duration}" )
if [[ -n "${region}" ]]; then
  args+=( -R "${region}" )
elif [[ -n "${display}" ]]; then
  args+=( -D "${display}" )
fi

screencapture "${args[@]}" "${out}"

if [[ ${print_json} -eq 1 ]]; then
  VIDEO_PATH="${out}" DISPLAY="${display}" REGION="${region}" DURATION="${duration}" \
  python3 - <<'PY'
import json
import os

payload = {
    "video_path": os.path.abspath(os.environ.get("VIDEO_PATH") or ""),
    "duration": float(os.environ.get("DURATION") or 0),
    "display": os.environ.get("DISPLAY") or None,
    "region": os.environ.get("REGION") or None,
}
print(json.dumps(payload))
PY
else
  echo "${out}"
fi
