#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOT'
extract_frames.sh

Extract frames from a video using ffmpeg.

Usage:
  extract_frames.sh <video.mov> [--out <dir>] [--fps <n>] [--json]

Options:
  --out   Output directory (default: .seer/record/frames-<video>-<ts>-<pid>-<rand>)
  --fps   Frames per second (default: 10)
  --json  Print JSON metadata to stdout

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)
EOT
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 ]]; then
  usage
  exit 0
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "error: ffmpeg not found"
  exit 1
fi

video="$1"
shift

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
frames_root_dir="${out_root}/record"
ts=$(date +%Y%m%d-%H%M%S)
run_id="${ts}-$$-$RANDOM"

out_dir=""
fps=10
print_json=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      out_dir="${2:-}"
      shift 2
      ;;
    --fps)
      fps="${2:-}"
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

if [[ ! -f "${video}" ]]; then
  echo "error: video not found: ${video}"
  exit 1
fi

base=$(basename "${video}")
slug="${base%.*}"

if [[ -z "${out_dir}" ]]; then
  out_dir="${frames_root_dir}/frames-${slug}-${run_id}"
fi

mkdir -p "${out_dir}"
ffmpeg -hide_banner -loglevel error -i "${video}" -vf "fps=${fps}" "${out_dir}/frame-%04d.png"

if [[ ${print_json} -eq 1 ]]; then
  VIDEO_PATH="${video}" OUT_DIR="${out_dir}" FPS="${fps}" \
  python3 - <<'PY'
import json
import os

payload = {
    "video_path": os.path.abspath(os.environ.get("VIDEO_PATH") or ""),
    "frames_dir": os.path.abspath(os.environ.get("OUT_DIR") or ""),
    "fps": float(os.environ.get("FPS") or 0),
}
print(json.dumps(payload))
PY
else
  echo "${out_dir}"
fi
