#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
loop_compare.sh

Usage:
  loop_compare.sh [options] <current_path> <baseline_name>

Options:
  --loop-dir <path>   Override loop storage directory (default: $SEER_LOOP_DIR or .seer)
  --resize            Resize current image to match baseline size
  --update-baseline   Replace baseline with current after comparison
  -h, --help          Show help

Behavior:
  - Stores latest, history, and diff images under the loop directory
  - Creates a baseline on first run
USAGE
}

loop_dir=${SEER_LOOP_DIR:-.seer}
resize=0
update_baseline=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --loop-dir)
      loop_dir="$2"
      shift 2
      ;;
    --resize)
      resize=1
      shift
      ;;
    --update-baseline)
      update_baseline=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

current=${1:-}
baseline_name=${2:-}

if [[ -z "${current}" || -z "${baseline_name}" ]]; then
  usage >&2
  exit 1
fi

if [[ ! -f "${current}" ]]; then
  echo "error: current image not found: ${current}" >&2
  exit 1
fi

safe_name=$(echo "${baseline_name}" | tr ' /:' '___' | tr -cd 'A-Za-z0-9._-')
if [[ -z "${safe_name}" ]]; then
  safe_name="baseline"
fi

ts=$(date +%Y%m%d-%H%M%S)

base_dir="${loop_dir}"
base_baselines="${base_dir}/baselines"
base_latest="${base_dir}/latest"
base_history="${base_dir}/history"
base_diffs="${base_dir}/diffs"
base_reports="${base_dir}/reports"

mkdir -p "${base_baselines}" "${base_latest}" "${base_history}" "${base_diffs}" "${base_reports}"

baseline_path="${base_baselines}/${safe_name}.png"
latest_path="${base_latest}/${safe_name}.png"
history_path="${base_history}/${safe_name}-${ts}.png"
diff_path="${base_diffs}/${safe_name}-${ts}.png"
json_path="${base_reports}/${safe_name}-${ts}.json"

cp -f "${current}" "${latest_path}"
cp -f "${current}" "${history_path}"

if [[ ! -f "${baseline_path}" ]]; then
  cp -f "${current}" "${baseline_path}"
  BASELINE_PATH="${baseline_path}" LATEST_PATH="${latest_path}" HISTORY_PATH="${history_path}" \
    python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "baseline_created": os.path.abspath(os.environ["BASELINE_PATH"]),
            "latest": os.path.abspath(os.environ["LATEST_PATH"]),
            "history": os.path.abspath(os.environ["HISTORY_PATH"]),
        }
    )
)
PY
  exit 0
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

compare_args=("${baseline_path}" "${current}" --diff-out "${diff_path}" --json-out "${json_path}")
if [[ ${resize} -eq 1 ]]; then
  compare_args+=(--resize)
fi

python3 "${script_dir}/compare_images.py" "${compare_args[@]}"

if [[ ${update_baseline} -eq 1 ]]; then
  cp -f "${current}" "${baseline_path}"
fi
