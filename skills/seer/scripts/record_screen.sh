#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOT'
record_screen.sh

Record the screen (or a region) using screencapture or ffmpeg.

Usage:
  record_screen.sh [--engine <screencapture|ffmpeg>] [--out <video.{mov|mp4}>]
                   [--duration <sec>] [--manual-stop] [--display <n>]
                   [--device-index <n>] [--region <x,y,w,h>]
                   [--framerate <fps>] [--capture-cursor] [--capture-clicks] [--json]

Options:
  --engine          Recording engine (default: screencapture)
  --out             Output video path (default: .seer/record/screen-<ts>-<pid>-<rand>.mov|mp4)
  --duration        Recording duration in seconds (default: 3; ignored with --manual-stop)
  --manual-stop     ffmpeg only; record until q / Ctrl-C instead of fixed duration
  --display         screencapture only; display index for full-screen capture (uses screencapture -D)
  --device-index    ffmpeg only; AVFoundation screen device index (auto-detects first "Capture screen")
  --region          Capture a rect as "x,y,w,h" (screencapture uses -R; ffmpeg crops the screen capture)
  --framerate       ffmpeg only; output framerate (default: 30)
  --capture-cursor  ffmpeg only; include cursor
  --capture-clicks  ffmpeg only; highlight mouse clicks
  --json            Print JSON metadata to stdout

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)
  SEER_RECORD_ENGINE default engine override
  SEER_FFMPEG_DEVICE_INDEX default AVFoundation device index override

Notes:
  - Requires Screen Recording permission for terminal.
  - If both --display and --region are provided, --region wins.
  - ffmpeg mode is better for long QA runs because it supports manual stop and direct mp4 output.
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

engine=${SEER_RECORD_ENGINE:-screencapture}
out=""
duration=3
display=""
device_index="${SEER_FFMPEG_DEVICE_INDEX:-}"
region=""
framerate=30
manual_stop=0
capture_cursor=0
capture_clicks=0
print_json=0

default_ffmpeg_screen_device_index() {
  if ! command -v ffmpeg >/dev/null 2>&1; then
    return 1
  fi

  ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | sed -n 's/.*\[\([0-9][0-9]*\)\] Capture screen.*/\1/p' \
    | head -n 1
}

run_screencapture() {
  local out_path="$1"
  local args=(-x -v -V "${duration}")
  if [[ -n "${region}" ]]; then
    args+=(-R "${region}")
  elif [[ -n "${display}" ]]; then
    args+=(-D "${display}")
  fi
  screencapture "${args[@]}" "${out_path}"
}

run_ffmpeg() {
  local out_path="$1"
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "error: ffmpeg not found"
    exit 1
  fi
  if [[ -n "${display}" ]]; then
    echo "error: --display is only supported with --engine screencapture"
    exit 1
  fi

  local ffmpeg_device="${device_index}"
  if [[ -z "${ffmpeg_device}" ]]; then
    ffmpeg_device="$(default_ffmpeg_screen_device_index || true)"
  fi
  if [[ -z "${ffmpeg_device}" ]]; then
    echo "error: could not auto-detect an AVFoundation screen device; pass --device-index"
    exit 1
  fi

  local filter_parts=()
  if [[ -n "${region}" ]]; then
    IFS=, read -r crop_x crop_y crop_w crop_h <<< "${region}"
    if [[ -z "${crop_x:-}" || -z "${crop_y:-}" || -z "${crop_w:-}" || -z "${crop_h:-}" ]]; then
      echo "error: region must be x,y,w,h"
      exit 1
    fi
    filter_parts+=("crop=${crop_w}:${crop_h}:${crop_x}:${crop_y}")
  fi

  # Keep ffmpeg output compact for QA recordings without upscaling smaller captures.
  filter_parts+=("scale='min(1800,iw)':-2")
  filter_parts+=("fps=${framerate}")

  local filter_graph
  filter_graph=$(IFS=,; echo "${filter_parts[*]}")

  local args=(
    -y
    -f avfoundation
    -framerate "${framerate}"
    -pixel_format uyvy422
  )
  if [[ ${capture_cursor} -eq 1 ]]; then
    args+=(-capture_cursor 1)
  fi
  if [[ ${capture_clicks} -eq 1 ]]; then
    args+=(-capture_mouse_clicks 1)
  fi
  args+=(-i "${ffmpeg_device}:none" -an)
  if [[ ${manual_stop} -eq 0 ]]; then
    args+=(-t "${duration}")
  fi
  args+=(-vf "${filter_graph}" -c:v libx264 -preset ultrafast -crf 25 -pix_fmt yuv420p "${out_path}")

  ffmpeg "${args[@]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine)
      engine="${2:-}"
      shift 2
      ;;
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
    --device-index)
      device_index="${2:-}"
      shift 2
      ;;
    --region)
      region="${2:-}"
      shift 2
      ;;
    --framerate)
      framerate="${2:-}"
      shift 2
      ;;
    --manual-stop)
      manual_stop=1
      shift
      ;;
    --capture-cursor)
      capture_cursor=1
      shift
      ;;
    --capture-clicks)
      capture_clicks=1
      shift
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
  case "${engine}" in
    screencapture) out="${record_dir}/screen-${run_id}.mov" ;;
    ffmpeg) out="${record_dir}/screen-${run_id}.mp4" ;;
    *)
      echo "error: unsupported engine: ${engine}"
      exit 1
      ;;
  esac
fi

mkdir -p "$(dirname "${out}")"

case "${engine}" in
  screencapture)
    if [[ ${manual_stop} -eq 1 ]]; then
      echo "error: --manual-stop is only supported with --engine ffmpeg"
      exit 1
    fi
    if [[ -n "${device_index}" ]]; then
      echo "error: --device-index is only supported with --engine ffmpeg"
      exit 1
    fi
    if [[ ${capture_cursor} -eq 1 || ${capture_clicks} -eq 1 ]]; then
      echo "error: --capture-cursor and --capture-clicks are only supported with --engine ffmpeg"
      exit 1
    fi
    run_screencapture "${out}"
    ;;
  ffmpeg)
    run_ffmpeg "${out}"
    ;;
  *)
    echo "error: unsupported engine: ${engine}"
    exit 1
    ;;
esac

if [[ ${print_json} -eq 1 ]]; then
  VIDEO_PATH="${out}" DISPLAY="${display}" REGION="${region}" DURATION="${duration}" ENGINE="${engine}" DEVICE_INDEX="${device_index}" \
  python3 - <<'PY'
import json
import os

payload = {
    "video_path": os.path.abspath(os.environ.get("VIDEO_PATH") or ""),
    "duration": float(os.environ.get("DURATION") or 0),
    "engine": os.environ.get("ENGINE") or None,
    "display": os.environ.get("DISPLAY") or None,
    "device_index": os.environ.get("DEVICE_INDEX") or None,
    "region": os.environ.get("REGION") or None,
}
print(json.dumps(payload))
PY
else
  echo "${out}"
fi
