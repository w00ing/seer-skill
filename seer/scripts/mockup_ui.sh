#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
mockup_ui.sh

Capture a window image (optional) and annotate it with arrows, rectangles, and text.

Usage:
  mockup_ui.sh --spec <spec.json> [--out <output.png>] [--process <app_name>] [--input <image.png>]

Options:
  --spec     Path to JSON spec (or - for stdin) [required]
  --out      Output PNG path (default: .seer/mockups/mockup-<app>-<ts>-<pid>-<rand>.png)
  --process  App process name to capture (default: frontmost app)
  --input    Existing image to annotate (skip capture)

Examples:
  bash scripts/mockup_ui.sh --spec spec.json
  bash scripts/mockup_ui.sh --spec spec.json --process "Promptlight"
  cat spec.json | bash scripts/mockup_ui.sh --spec - --input /tmp/screen.png

Env:
  SEER_OUT_DIR override default output root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

spec=""
out=""
process=""
input=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --spec)
      spec="${2:-}"
      shift 2
      ;;
    --out)
      out="${2:-}"
      shift 2
      ;;
    --process)
      process="${2:-}"
      shift 2
      ;;
    --input)
      input="${2:-}"
      shift 2
      ;;
    *)
      echo "error: unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${spec}" ]]; then
  echo "error: --spec is required"
  usage
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
ts=$(date +%Y%m%d-%H%M%S)
run_id="${ts}-$$-$RANDOM"

app_name=""
if [[ -n "${process}" ]]; then
  app_name="${process}"
elif [[ -z "${input}" ]]; then
  app_name=$(osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' 2>/dev/null || true)
fi

slug_source="${app_name}"
if [[ -z "${slug_source}" && -n "${input}" ]]; then
  base_name=$(basename "${input}")
  slug_source="${base_name%.*}"
fi

slug=$(echo "${slug_source}" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9._-')
if [[ -z "${slug}" ]]; then
  slug="app"
fi

if [[ -z "${input}" ]]; then
  capture_out="${out_root}/captures/app-window-${slug}-${run_id}.png"
  if [[ -n "${app_name}" ]]; then
    input=$(bash "${script_dir}/capture_app_window.sh" "${capture_out}" "${app_name}")
  else
    input=$(bash "${script_dir}/capture_app_window.sh" "${capture_out}")
  fi
fi

spec_dir="${out_root}/specs"
mockup_dir="${out_root}/mockups"
report_dir="${out_root}/reports"
latest_dir="${out_root}/latest"

mkdir -p "${spec_dir}" "${mockup_dir}" "${report_dir}" "${latest_dir}"

spec_path="${spec_dir}/spec-${slug}-${run_id}.json"
if [[ "${spec}" == "-" ]]; then
  cat > "${spec_path}"
  spec="${spec_path}"
else
  if [[ ! -f "${spec}" ]]; then
    echo "error: spec not found: ${spec}"
    exit 1
  fi
  cp -f "${spec}" "${spec_path}"
  spec="${spec_path}"
fi

if [[ -z "${out}" ]]; then
  out="${mockup_dir}/mockup-${slug}-${run_id}.png"
fi

annotated_path=$(python3 "${script_dir}/annotate_image.py" "${input}" "${out}" --spec "${spec}")

meta_path="${report_dir}/mockup-${slug}-${run_id}.json"
CAPTURE_PATH="${input}" SPEC_PATH="${spec}" OUTPUT_PATH="${annotated_path}" META_PATH="${meta_path}" \
APP_NAME="${app_name}" APP_SLUG="${slug}" RUN_ID="${run_id}" \
python3 - <<'PY'
import json
import os
import sys

def _abspath(value: str) -> str:
    return os.path.abspath(value) if value else ""

payload = {
    "app_name": os.environ.get("APP_NAME") or None,
    "app_slug": os.environ.get("APP_SLUG") or None,
    "run_id": os.environ.get("RUN_ID") or None,
    "capture_path": _abspath(os.environ.get("CAPTURE_PATH") or ""),
    "spec_path": _abspath(os.environ.get("SPEC_PATH") or ""),
    "output_path": _abspath(os.environ.get("OUTPUT_PATH") or ""),
}

meta_path = os.environ.get("META_PATH")
if meta_path:
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
print(json.dumps(payload), file=sys.stderr)
PY

cp -f "${input}" "${latest_dir}/capture-${slug}.png"
cp -f "${annotated_path}" "${latest_dir}/mockup-${slug}.png"
cp -f "${spec}" "${latest_dir}/spec-${slug}.json"
cp -f "${meta_path}" "${latest_dir}/mockup-${slug}.json"

echo "${annotated_path}"
