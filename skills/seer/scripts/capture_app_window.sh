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

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: required command not found: python3 (PNG validation requires Pillow)" >&2
  exit 2
fi

if ! command -v screencapture >/dev/null 2>&1; then
  echo "error: required command not found: screencapture" >&2
  exit 2
fi
if ! command -v osascript >/dev/null 2>&1; then
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

# Keep the destination untouched until this invocation produces a complete PNG.
# A sibling temporary directory makes the final replacement atomic.
capture_tmp_dir=
cleanup() {
  if [[ -n "${capture_tmp_dir}" ]]; then
    rm -f -- "${capture_tmp_dir}/current.png"
    rmdir -- "${capture_tmp_dir}"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

prepare_output() {
  local parent
  parent=$(dirname -- "${out}")
  if ! mkdir -p -- "${parent}" || ! capture_tmp_dir=$(mktemp -d "${parent}/.seer-capture.XXXXXX"); then
    echo "error: could not prepare capture output: ${out}" >&2
    exit 2
  fi
}

publish_capture() {
  if python3 - "${capture_tmp_dir}/current.png" "${out}" <<'PY'
import os
import sys

try:
    from PIL import Image
except ImportError:
    print("error: Pillow is required for PNG validation; install it in the active python3 environment", file=sys.stderr)
    raise SystemExit(2)

try:
    with Image.open(sys.argv[1]) as image:
        if image.format != "PNG":
            raise ValueError("capture output is not a PNG")
        image.verify()
    # verify() checks the container; load() also decodes the actual pixel data.
    with Image.open(sys.argv[1]) as image:
        image.load()
    os.replace(sys.argv[1], sys.argv[2])
except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
    print(f"error: could not validate or publish capture: {exc}", file=sys.stderr)
    raise SystemExit(2)
PY
  then
    echo "${out}"
  else
    exit 2
  fi
}

require_visible_window() {
  # screencapture can return cached pixels for a closed window on some macOS
  # versions. Confirm the ID is still on-screen before and after capture.
  if ! osascript -l JavaScript - "${window_id}" >/dev/null <<'JXA'
ObjC.import("CoreGraphics");
function run(argv) {
  const raw = $.CGWindowListCopyWindowInfo(
    $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0
  );
  if (!raw) throw new Error("window server unavailable");
  const windows = ObjC.deepUnwrap(ObjC.castRefToObject(raw));
  const visible = windows.some(window =>
    Number(window.kCGWindowNumber) === Number(argv[0]) &&
    Number(window.kCGWindowLayer) === 0 && window.kCGWindowIsOnscreen !== false &&
    Number(window.kCGWindowAlpha) > 0 && Number(window.kCGWindowSharingState) !== 0
  );
  if (!visible) throw new Error("window is not visible");
}
JXA
  then
    echo "error: window ID ${window_id} is unavailable; existing output was preserved" >&2
    echo "hint: rerun 'seer windows --json'; IDs expire when a window closes or is recreated" >&2
    exit 2
  fi
}

if [[ -n "${window_id}" ]]; then
  require_visible_window
  prepare_output
  if ! screencapture -x -o -a -t png "-l${window_id}" "${capture_tmp_dir}/current.png"; then
    echo "error: window ID ${window_id} is unavailable or screen capture permission is missing" >&2
    echo "hint: rerun 'seer windows --json'; IDs expire when a window closes or is recreated" >&2
    exit 2
  fi
  require_visible_window
  publish_capture
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

prepare_output
if ! screencapture -x -t png -R "${x},${y},${w},${h}" "${capture_tmp_dir}/current.png"; then
  echo "error: screen capture failed; verify Screen Recording permission" >&2
  exit 2
fi

publish_capture
