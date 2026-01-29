#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOT'
summarize_video.sh

Create a compact set of representative frames from a video, with optional contact sheet/GIF.

Usage:
  summarize_video.sh <video.mov> [--out <dir>] [--mode <scene|fps|keyframes>] [--scene <threshold>]
                       [--fps <n>] [--max <n>] [--sheet] [--sheet-cols <n>] [--gif]
                       [--gif-width <px>] [--json]

Options:
  --out         Output directory (default: .seer/record/summary-<video>-<ts>-<pid>-<rand>)
  --mode        Frame selection mode: scene | fps | keyframes (default: scene)
  --scene       Scene-change threshold for mode=scene (default: 0.30)
  --fps         Frames per second for mode=fps (default: 2)
  --max         Max frames to keep (default: 24, 0 disables cap)
  --sheet       Generate a contact sheet (sheet.png)
  --sheet-cols  Columns for contact sheet (default: auto)
  --gif         Generate a preview GIF (preview.gif)
  --gif-width   Max GIF width in pixels (default: 640)
  --json        Print JSON metadata to stdout

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)

Notes:
  - Requires ffmpeg + ffprobe.
  - For scene mode, lower --scene if no frames are produced.
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

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "error: ffprobe not found"
  exit 1
fi

video="$1"
shift

if [[ ! -f "${video}" ]]; then
  echo "error: video not found: ${video}"
  exit 1
fi

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
summary_root_dir="${out_root}/record"
ts=$(date +%Y%m%d-%H%M%S)
run_id="${ts}-$$-$RANDOM"

out_dir=""
mode="scene"
scene_threshold="0.30"
fps="2"
max_frames="24"
make_sheet=0
sheet_cols=""
make_gif=0
gif_width="640"
print_json=0
mode_requested=""
mode_used=""
fallback_used=0
gif_error=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      out_dir="${2:-}"
      shift 2
      ;;
    --mode)
      mode="${2:-}"
      shift 2
      ;;
    --scene)
      scene_threshold="${2:-}"
      shift 2
      ;;
    --fps)
      fps="${2:-}"
      shift 2
      ;;
    --max)
      max_frames="${2:-}"
      shift 2
      ;;
    --sheet)
      make_sheet=1
      shift
      ;;
    --sheet-cols)
      sheet_cols="${2:-}"
      shift 2
      ;;
    --gif)
      make_gif=1
      shift
      ;;
    --gif-width)
      gif_width="${2:-}"
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

mode_requested="${mode}"
mode_used="${mode}"

base=$(basename "${video}")
slug="${base%.*}"

if [[ -z "${out_dir}" ]]; then
  out_dir="${summary_root_dir}/summary-${slug}-${run_id}"
fi

mkdir -p "${out_dir}"

extract_frames_for_mode() {
  local extract_mode="$1"
  rm -f "${out_dir}"/frame-*.png
  case "${extract_mode}" in
    scene)
      ffmpeg -hide_banner -loglevel error -i "${video}" \
        -vf "select='eq(n,0)+gt(scene,${scene_threshold})'" -vsync vfr \
        "${out_dir}/frame-%04d.png"
      ;;
    fps)
      ffmpeg -hide_banner -loglevel error -i "${video}" -vf "fps=${fps}" \
        "${out_dir}/frame-%04d.png"
      ;;
    keyframes)
      ffmpeg -hide_banner -loglevel error -skip_frame nokey -i "${video}" -vsync vfr \
        "${out_dir}/frame-%04d.png"
      ;;
    *)
      echo "error: unknown mode: ${extract_mode} (use scene, fps, or keyframes)"
      exit 1
      ;;
  esac
}

count_frames() {
  OUT_DIR="${out_dir}" python3 - <<'PY'
import glob
import os

out_dir = os.environ.get("OUT_DIR") or ""
frames = sorted(glob.glob(os.path.join(out_dir, "frame-*.png")))
print(len(frames))
PY
}

extract_frames_for_mode "${mode_used}"

frame_count=$(count_frames)

if [[ "${mode_used}" == "scene" && "${frame_count}" -lt 2 ]]; then
  echo "warn: scene mode produced ${frame_count} frame(s); falling back to fps=${fps}" >&2
  mode_used="fps"
  fallback_used=1
  extract_frames_for_mode "${mode_used}"
  frame_count=$(count_frames)
fi

if [[ "${frame_count}" -eq 0 ]]; then
  echo "error: no frames extracted (try lower --scene or use --mode fps)"
  exit 1
fi

if [[ "${max_frames}" -gt 0 && "${frame_count}" -gt "${max_frames}" ]]; then
  frame_count=$(OUT_DIR="${out_dir}" MAX_FRAMES="${max_frames}" python3 - <<'PY'
import glob
import os

out_dir = os.environ.get("OUT_DIR") or ""
max_frames = int(os.environ.get("MAX_FRAMES") or 0)

frames = sorted(glob.glob(os.path.join(out_dir, "frame-*.png")))
if max_frames <= 0 or len(frames) <= max_frames:
    print(len(frames))
    raise SystemExit(0)

keep = set()
total = len(frames)
for i in range(max_frames):
    idx = round(i * (total - 1) / (max_frames - 1)) if max_frames > 1 else 0
    keep.add(frames[idx])

for frame in frames:
    if frame not in keep:
        try:
            os.remove(frame)
        except FileNotFoundError:
            pass

print(len(keep))
PY
  )
fi

sheet_path=""
if [[ ${make_sheet} -eq 1 ]]; then
  dims=$(FRAME_COUNT="${frame_count}" SHEET_COLS="${sheet_cols}" python3 - <<'PY'
import math
import os

frame_count = int(os.environ.get("FRAME_COUNT") or 0)
sheet_cols = os.environ.get("SHEET_COLS") or ""

if frame_count <= 0:
    print("0 0")
    raise SystemExit(0)

if sheet_cols:
    cols = max(1, int(sheet_cols))
else:
    cols = int(math.ceil(math.sqrt(frame_count)))
rows = int(math.ceil(frame_count / cols))
print(f"{cols} {rows}")
PY
  )

  cols=${dims%% *}
  rows=${dims##* }
  if [[ "${cols}" -gt 0 && "${rows}" -gt 0 ]]; then
    sheet_path="${out_dir}/sheet.png"
    ffmpeg -hide_banner -loglevel error -pattern_type glob -i "${out_dir}/frame-*.png" \
      -vf "tile=${cols}x${rows}" "${sheet_path}"
  fi
fi

gif_path=""
if [[ ${make_gif} -eq 1 ]]; then
  gif_path="${out_dir}/preview.gif"
  gif_fps=12
  if [[ "${frame_count}" -lt 2 ]]; then
    gif_fps=1
  fi
  palette="${out_dir}/palette.png"
  set +e
  ffmpeg -hide_banner -loglevel error -pattern_type glob -i "${out_dir}/frame-*.png" \
    -vf "fps=${gif_fps},scale=${gif_width}:-1:flags=lanczos,palettegen" -y "${palette}"
  palette_status=$?
  if [[ ${palette_status} -eq 0 ]]; then
    ffmpeg -hide_banner -loglevel error -pattern_type glob -i "${out_dir}/frame-*.png" -i "${palette}" \
      -lavfi "fps=${gif_fps},scale=${gif_width}:-1:flags=lanczos[x];[x][1:v]paletteuse" -y "${gif_path}"
    gif_status=$?
    if [[ ${gif_status} -ne 0 ]]; then
      gif_error="gif encode failed (exit ${gif_status})"
    fi
  else
    gif_error="gif palette generation failed (exit ${palette_status})"
  fi
  set -e
  rm -f "${palette}"
  if [[ -n "${gif_error}" || ! -s "${gif_path}" ]]; then
    if [[ -z "${gif_error}" ]]; then
      gif_error="gif file not created"
    fi
    echo "warn: ${gif_error}" >&2
    gif_path=""
  fi
fi

duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "${video}" || true)

if [[ ${print_json} -eq 1 ]]; then
  VIDEO_PATH="${video}" OUT_DIR="${out_dir}" MODE_REQUESTED="${mode_requested}" MODE_USED="${mode_used}" FALLBACK_USED="${fallback_used}" \
  SCENE="${scene_threshold}" FPS="${fps}" MAX_FRAMES="${max_frames}" FRAME_COUNT="${frame_count}" SHEET_PATH="${sheet_path}" \
  GIF_PATH="${gif_path}" GIF_ERROR="${gif_error}" DURATION="${duration}" \
  python3 - <<'PY'
import json
import os

def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

payload = {
    "video_path": os.path.abspath(os.environ.get("VIDEO_PATH") or ""),
    "frames_dir": os.path.abspath(os.environ.get("OUT_DIR") or ""),
    "mode_requested": os.environ.get("MODE_REQUESTED") or None,
    "mode_used": os.environ.get("MODE_USED") or None,
    "fallback_used": os.environ.get("FALLBACK_USED") == "1",
    "scene_threshold": to_float(os.environ.get("SCENE")),
    "fps": to_float(os.environ.get("FPS")),
    "max_frames": int(os.environ.get("MAX_FRAMES") or 0),
    "frame_count": int(os.environ.get("FRAME_COUNT") or 0),
    "sheet_path": os.path.abspath(os.environ.get("SHEET_PATH") or "") if os.environ.get("SHEET_PATH") else None,
    "gif_path": os.path.abspath(os.environ.get("GIF_PATH") or "") if os.environ.get("GIF_PATH") else None,
    "gif_error": os.environ.get("GIF_ERROR") or None,
    "duration": to_float(os.environ.get("DURATION")),
}
print(json.dumps(payload))
PY
else
  echo "${out_dir}"
  if [[ -n "${sheet_path}" ]]; then
    echo "${sheet_path}"
  fi
  if [[ -n "${gif_path}" ]]; then
    echo "${gif_path}"
  fi
fi
