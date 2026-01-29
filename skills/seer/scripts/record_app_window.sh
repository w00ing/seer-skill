#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOT'
record_app_window.sh

Record a macOS app window as a .mov using screencapture.

Usage:
  record_app_window.sh [--out <video.mov>] [--process <app_name>] [--simulator] [--activate] [--no-activate]
                       [--duration <sec>] [--frames] [--fps <n>] [--frames-dir <dir>]
                       [--summary] [--summary-mode <scene|fps|keyframes>] [--summary-scene <threshold>] [--summary-fps <n>]
                       [--summary-max <n>] [--summary-out <dir>] [--summary-sheet] [--summary-sheet-cols <n>]
                       [--summary-gif] [--summary-gif-width <px>] [--json]

Options:
  --out         Output video path (default: .seer/record/app-window-<app>-<ts>-<pid>-<rand>.mov)
  --process     App process name to capture (default: frontmost app)
  --simulator   Convenience flag for iOS Simulator (same as --process Simulator)
  --activate    Activate target app before recording
  --no-activate Do not activate target app (default: auto-activate when --process/--simulator is used)
  --duration    Recording duration in seconds (default: 3)
  --frames      Extract frames after recording
  --fps         Frames per second for extraction (default: 10)
  --frames-dir  Output dir for extracted frames (default: .seer/record/frames-<app>-<ts>-<pid>-<rand>)
  --summary     Generate representative frames from the video (uses summarize_video.sh)
  --summary-mode     Frame selection mode: scene | fps | keyframes (default: scene)
  --summary-scene    Scene-change threshold for mode=scene (default: 0.30)
  --summary-fps      Frames per second for mode=fps (default: 2)
  --summary-max      Max frames to keep (default: 24, 0 disables cap)
  --summary-out      Output dir for summary frames (default: .seer/record/summary-<video>-<ts>-<pid>-<rand>)
  --summary-sheet    Generate a contact sheet (sheet.png)
  --summary-sheet-cols  Columns for contact sheet (default: auto)
  --summary-gif      Generate a preview GIF (preview.gif)
  --summary-gif-width Max GIF width in pixels (default: 640)
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
run_summary=0
summary_mode="scene"
summary_scene="0.30"
summary_fps="2"
summary_max="24"
summary_out=""
summary_sheet=0
summary_sheet_cols=""
summary_gif=0
summary_gif_width="640"
print_json=0
activate=0
activate_set=0
process_set=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      out="${2:-}"
      shift 2
      ;;
    --process)
      process="${2:-}"
      process_set=1
      shift 2
      ;;
    --simulator)
      process="Simulator"
      process_set=1
      if [[ ${activate_set} -eq 0 ]]; then
        activate=1
      fi
      shift
      ;;
    --activate)
      activate=1
      activate_set=1
      shift
      ;;
    --no-activate)
      activate=0
      activate_set=1
      shift
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
    --summary)
      run_summary=1
      shift
      ;;
    --summary-mode)
      summary_mode="${2:-}"
      shift 2
      ;;
    --summary-scene)
      summary_scene="${2:-}"
      shift 2
      ;;
    --summary-fps)
      summary_fps="${2:-}"
      shift 2
      ;;
    --summary-max)
      summary_max="${2:-}"
      shift 2
      ;;
    --summary-out)
      summary_out="${2:-}"
      shift 2
      ;;
    --summary-sheet)
      summary_sheet=1
      shift
      ;;
    --summary-sheet-cols)
      summary_sheet_cols="${2:-}"
      shift 2
      ;;
    --summary-gif)
      summary_gif=1
      shift
      ;;
    --summary-gif-width)
      summary_gif_width="${2:-}"
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

frontmost=$(osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' 2>/dev/null || true)

if [[ -z "${process}" ]]; then
  process="${frontmost}"
elif [[ ${activate_set} -eq 0 ]]; then
  activate=1
fi

if [[ -n "${process}" && -n "${frontmost}" && "${process}" != "${frontmost}" ]]; then
  echo "note: frontmost app is '${frontmost}', targeting '${process}'" >&2
fi

if [[ ${activate} -eq 1 && -n "${process}" ]]; then
  osascript -e "tell application \"${process}\" to activate" >/dev/null 2>&1 || true
  sleep 0.2
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

echo "seer: app='${process}' out='${out}' duration=${duration}s bounds=${x},${y},${w},${h}" >&2
if [[ ${extract_frames} -eq 1 ]]; then
  echo "seer: frames -> ${frames_dir} (fps=${fps})" >&2
fi
if [[ ${run_summary} -eq 1 ]]; then
  echo "seer: summary -> mode=${summary_mode} max=${summary_max}" >&2
fi

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

summary_json=""
summary_dir=""
summary_sheet_path=""
summary_gif_path=""
summary_error=""
if [[ ${run_summary} -eq 1 ]]; then
  script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  summary_script="${script_dir}/summarize_video.sh"
  if [[ ! -x "${summary_script}" ]]; then
    echo "error: summarize_video.sh not found or not executable: ${summary_script}"
    exit 1
  fi

  summary_args=( "${out}" "--mode" "${summary_mode}" "--scene" "${summary_scene}" "--fps" "${summary_fps}" "--max" "${summary_max}" )
  if [[ -n "${summary_out}" ]]; then
    summary_args+=( "--out" "${summary_out}" )
  fi
  if [[ ${summary_sheet} -eq 1 ]]; then
    summary_args+=( "--sheet" )
  fi
  if [[ -n "${summary_sheet_cols}" ]]; then
    summary_args+=( "--sheet-cols" "${summary_sheet_cols}" )
  fi
  if [[ ${summary_gif} -eq 1 ]]; then
    summary_args+=( "--gif" )
  fi
  if [[ -n "${summary_gif_width}" ]]; then
    summary_args+=( "--gif-width" "${summary_gif_width}" )
  fi

  if [[ ${print_json} -eq 1 ]]; then
    set +e
    summary_json=$("${summary_script}" "${summary_args[@]}" --json)
    summary_status=$?
    set -e
  else
    set +e
    summary_lines=()
    while IFS= read -r line; do
      summary_lines+=("${line}")
    done < <("${summary_script}" "${summary_args[@]}")
    summary_status=$?
    set -e
    summary_dir="${summary_lines[0]:-}"
    summary_sheet_path="${summary_lines[1]:-}"
    summary_gif_path="${summary_lines[2]:-}"
  fi
  if [[ ${summary_status:-0} -ne 0 ]]; then
    summary_error="summarize_video.sh failed (exit ${summary_status})"
    echo "warn: ${summary_error}" >&2
  fi
fi

if [[ ${print_json} -eq 1 ]]; then
  VIDEO_PATH="${out}" FRAMES_DIR="${frames_out}" APP_NAME="${process}" APP_SLUG="${slug}" DURATION="${duration}" FPS="${fps}" SUMMARY_JSON="${summary_json}" SUMMARY_ERROR="${summary_error}" \
  python3 - <<'PY'
import json
import os

summary_json = os.environ.get("SUMMARY_JSON") or ""
try:
    summary = json.loads(summary_json) if summary_json else None
except json.JSONDecodeError:
    summary = None

payload = {
    "app_name": os.environ.get("APP_NAME") or None,
    "app_slug": os.environ.get("APP_SLUG") or None,
    "duration": float(os.environ.get("DURATION") or 0),
    "fps": float(os.environ.get("FPS") or 0),
    "video_path": os.path.abspath(os.environ.get("VIDEO_PATH") or ""),
    "frames_dir": os.path.abspath(os.environ.get("FRAMES_DIR") or "") if os.environ.get("FRAMES_DIR") else None,
    "summary": summary,
    "summary_error": os.environ.get("SUMMARY_ERROR") or None,
}
print(json.dumps(payload))
PY
else
  echo "${out}"
  if [[ -n "${frames_out}" ]]; then
    echo "${frames_out}"
  fi
  if [[ -n "${summary_dir}" ]]; then
    echo "${summary_dir}"
  fi
  if [[ -n "${summary_sheet_path}" ]]; then
    echo "${summary_sheet_path}"
  fi
  if [[ -n "${summary_gif_path}" ]]; then
    echo "${summary_gif_path}"
  fi
fi
