#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
loop_compare.sh

Usage:
  loop_compare.sh [options] <current_path> <baseline_name>

Options:
  --loop-dir <path>   Override loop storage directory (default: $SEER_LOOP_DIR or .seer/loop)
  --resize            Resize current image to match baseline size
  --max-diff-percent <n>
                      Maximum changed pixels allowed, 0-100 (default: 0)
  --create-baseline   Create a missing baseline from current
  --update-baseline   Replace baseline with current after comparison
  -h, --help          Show help

Behavior:
  - Stores latest, history, and diff images under the loop directory
  - Returns needs_baseline (exit 3) when the baseline is missing
  - Creates baselines only with --create-baseline
USAGE
}

out_root=${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}
loop_dir=${SEER_LOOP_DIR:-"${out_root}/loop"}

# Backward-compat: if the legacy layout exists and the new one doesn't, keep using legacy by default.
if [[ -z "${SEER_LOOP_DIR:-}" && -d "${out_root}/baselines" && ! -d "${out_root}/loop/baselines" ]]; then
  loop_dir="${out_root}"
fi
resize=0
max_diff_percent=0
create_baseline=0
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
    --max-diff-percent)
      if [[ $# -lt 2 ]]; then
        echo "error: --max-diff-percent requires a value" >&2
        exit 2
      fi
      max_diff_percent="$2"
      shift 2
      ;;
    --create-baseline)
      create_baseline=1
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

if [[ ${create_baseline} -eq 1 && ${update_baseline} -eq 1 ]]; then
  echo "error: --create-baseline and --update-baseline cannot be used together" >&2
  exit 1
fi

validated_threshold=$(python3 - "${max_diff_percent}" <<'PY'
import math
import sys

try:
    value = float(sys.argv[1])
except ValueError:
    value = math.nan

if not math.isfinite(value) or not 0 <= value <= 100:
    print("error: --max-diff-percent must be a finite number between 0 and 100", file=sys.stderr)
    raise SystemExit(2)

print(value)
PY
)
max_diff_percent="${validated_threshold}"

safe_name=$(echo "${baseline_name}" | tr ' /:' '___' | tr -cd 'A-Za-z0-9._-')
if [[ -z "${safe_name}" ]]; then
  safe_name="baseline"
fi

ts=$(date +%Y%m%d-%H%M%S)-$$

base_dir="${loop_dir}"
base_baselines="${base_dir}/baselines"
base_latest="${base_dir}/latest"
base_history="${base_dir}/history"
base_diffs="${base_dir}/diffs"
base_reports="${base_dir}/reports"

baseline_path="${base_baselines}/${safe_name}.png"
latest_path="${base_latest}/${safe_name}.png"
history_path="${base_history}/${safe_name}-${ts}.png"
diff_path="${base_diffs}/${safe_name}-${ts}.png"
json_path="${base_reports}/${safe_name}-${ts}.json"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [[ ! -f "${baseline_path}" ]]; then
  if python3 "${script_dir}/compare_images.py" "${current}" "${current}" >/dev/null; then
    :
  else
    exit $?
  fi

  if [[ ${create_baseline} -ne 1 ]]; then
    BASELINE_PATH="${baseline_path}" CURRENT_PATH="${current}" MAX_DIFF_PERCENT="${max_diff_percent}" python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "schema_version": 1,
            "operation": "verify",
            "status": "needs_baseline",
            "baseline": os.path.abspath(os.environ["BASELINE_PATH"]),
            "current": os.path.abspath(os.environ["CURRENT_PATH"]),
            "thresholds": {"max_diff_percent": float(os.environ["MAX_DIFF_PERCENT"])},
            "next_action": "Rerun with --create-baseline after explicit approval.",
        }
    )
)
PY
    exit 3
  fi

  mkdir -p "${base_baselines}" "${base_latest}" "${base_history}"
  cp -f "${current}" "${baseline_path}"
  cp -f "${current}" "${latest_path}"
  cp -f "${current}" "${history_path}"
  BASELINE_PATH="${baseline_path}" LATEST_PATH="${latest_path}" HISTORY_PATH="${history_path}" \
    python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "schema_version": 1,
            "operation": "baseline_create",
            "status": "pass",
            "baseline_created": os.path.abspath(os.environ["BASELINE_PATH"]),
            "latest": os.path.abspath(os.environ["LATEST_PATH"]),
            "history": os.path.abspath(os.environ["HISTORY_PATH"]),
        }
    )
)
PY
  exit 0
fi

if [[ ${create_baseline} -eq 1 ]]; then
  echo "error: baseline already exists: ${baseline_path}; use --update-baseline to replace it" >&2
  exit 1
fi

compare_args=(
  "${baseline_path}"
  "${current}"
  --diff-out "${diff_path}"
  --json-out "${json_path}"
  --max-diff-percent "${max_diff_percent}"
)
if [[ ${resize} -eq 1 ]]; then
  compare_args+=(--resize)
fi

if python3 "${script_dir}/compare_images.py" "${compare_args[@]}" >/dev/null; then
  compare_exit=0
else
  compare_exit=$?
fi

if [[ ${compare_exit} -ne 0 && ${compare_exit} -ne 1 ]]; then
  exit "${compare_exit}"
fi

if [[ ! -f "${json_path}" ]]; then
  echo "error: comparison did not produce a JSON report" >&2
  exit 2
fi

mkdir -p "${base_latest}" "${base_history}"
cp -f "${current}" "${latest_path}"
cp -f "${current}" "${history_path}"

if [[ ${update_baseline} -eq 1 ]]; then
  baseline_tmp="${baseline_path}.tmp.$$"
  cp -f "${current}" "${baseline_tmp}"
  mv -f "${baseline_tmp}" "${baseline_path}"
fi

REPORT_PATH="${json_path}" LATEST_PATH="${latest_path}" HISTORY_PATH="${history_path}" \
  BASELINE_UPDATED="${update_baseline}" python3 - <<'PY'
import json
import os

report_path = os.path.abspath(os.environ["REPORT_PATH"])
with open(report_path, encoding="utf-8") as report_file:
    payload = json.load(report_file)

payload.update(
    {
        "artifacts": {
            "baseline": payload["baseline"],
            "current": payload["current"],
            "diff": payload["diff_image"],
            "report": report_path,
            "latest": os.path.abspath(os.environ["LATEST_PATH"]),
            "history": os.path.abspath(os.environ["HISTORY_PATH"]),
        },
        "baseline_updated": os.environ["BASELINE_UPDATED"] == "1",
    }
)

with open(report_path, "w", encoding="utf-8") as report_file:
    json.dump(payload, report_file, indent=2)

print(json.dumps(payload))
PY

exit "${compare_exit}"
