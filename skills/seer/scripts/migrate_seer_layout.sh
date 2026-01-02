#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
migrate_seer_layout.sh

Migrates legacy `.seer/` folder layout into a "one folder per feature" structure:

  .seer/
    capture/     (window screenshots)
    record/      (window recordings + extracted frames)
    mockup/      (annotated mockups + their capture/spec/meta)
    excalidraw/  (generated `.excalidraw` scenes + library cache)
    loop/        (baselines/latest/history/diffs/reports)

Usage:
  migrate_seer_layout.sh [--root <dir>]

Env:
  SEER_OUT_DIR override default root (default: .seer)
  SEER_TMP_DIR legacy override for output root (used if SEER_OUT_DIR is unset)
EOF
}

root="${SEER_OUT_DIR:-${SEER_TMP_DIR:-.seer}}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      root="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown arg: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${root}" ]]; then
  echo "error: root is empty" >&2
  exit 1
fi

artifact_root="${root}/artifacts"
capture_root="${root}/capture"
record_root="${root}/record"
mockup_root="${root}/mockup"
excalidraw_root="${root}/excalidraw"
loop_root="${root}/loop"

mkdir -p "${capture_root}" "${record_root}" "${mockup_root}" "${excalidraw_root}" "${loop_root}"

move_dir_merge() {
  local from="$1"
  local to="$2"
  if [[ "${from}" == "${to}" ]]; then
    return 0
  fi
  if [[ ! -d "${from}" ]]; then
    return 0
  fi
  mkdir -p "${to}"
  # Move contents (non-destructive: don't overwrite).
  if compgen -G "${from}/*" >/dev/null 2>&1; then
    find "${from}" -mindepth 1 -maxdepth 1 -exec mv -n {} "${to}/" \;
  fi
  rmdir "${from}" 2>/dev/null || true
}

# Artifacts
move_dir_merge "${root}/captures"           "${capture_root}"
move_dir_merge "${artifact_root}/captures"  "${capture_root}"

# Recordings + frames (merge into record/)
move_dir_merge "${root}/recordings"           "${record_root}"
move_dir_merge "${artifact_root}/recordings"  "${record_root}"
move_dir_merge "${root}/frames"               "${record_root}"
move_dir_merge "${artifact_root}/frames"      "${record_root}"

# Mockups + specs
move_dir_merge "${root}/mockups"          "${mockup_root}"
move_dir_merge "${artifact_root}/mockups" "${mockup_root}"
move_dir_merge "${root}/specs"            "${mockup_root}"
move_dir_merge "${artifact_root}/specs"   "${mockup_root}"

# Excalidraw scenes (and any "wireframes" folder)
move_dir_merge "${root}/excalidraw"          "${excalidraw_root}"
move_dir_merge "${artifact_root}/excalidraw" "${excalidraw_root}"
move_dir_merge "${root}/wireframes"          "${excalidraw_root}"
move_dir_merge "${artifact_root}/wireframes" "${excalidraw_root}"

# Visual loop (legacy at root -> loop/)
move_dir_merge "${root}/baselines" "${loop_root}/baselines"
move_dir_merge "${root}/history"   "${loop_root}/history"
move_dir_merge "${root}/diffs"     "${loop_root}/diffs"

# Split legacy reports/ between artifacts and loop (old layout mixed both).
if [[ -d "${root}/reports" || -d "${artifact_root}/reports" ]]; then
  mkdir -p "${mockup_root}" "${loop_root}/reports"
  shopt -s nullglob
  for f in "${root}/reports"/*.json "${artifact_root}/reports"/*.json; do
    [[ -f "${f}" ]] || continue
    base="$(basename "${f}")"
    if [[ "${base}" == mockup-*.json ]]; then
      mv -n "${f}" "${mockup_root}/" || true
    elif [[ "${base}" =~ ^.+-[0-9]{8}-[0-9]{6}\.json$ ]]; then
      mv -n "${f}" "${loop_root}/reports/" || true
    else
      mv -n "${f}" "${mockup_root}/" || true
    fi
  done
  shopt -u nullglob
  rmdir "${root}/reports" 2>/dev/null || true
  rmdir "${artifact_root}/reports" 2>/dev/null || true
fi

# Move legacy latest/ into feature folders (best-effort)
if [[ -d "${root}/latest" ]]; then
  shopt -s nullglob
  for f in "${root}/latest"/*; do
    [[ -f "${f}" ]] || continue
    base="$(basename "${f}")"
    if [[ "${base}" == nl-*.excalidraw ]]; then
      mv -n "${f}" "${excalidraw_root}/" || true
    elif [[ "${base}" == capture-*.png || "${base}" == mockup-*.png || "${base}" == spec-*.json || "${base}" == mockup-*.json ]]; then
      mv -n "${f}" "${mockup_root}/" || true
    else
      mv -n "${f}" "${mockup_root}/" || true
    fi
  done
  shopt -u nullglob
  rmdir "${root}/latest" 2>/dev/null || true
fi

# Move previous node caches from experiments into excalidraw/ (kept, not deleted)
node_cache="${excalidraw_root}/_cache"
mkdir -p "${node_cache}"
for d in "${root}"/.excalidraw-node*; do
  if [[ -d "${d}" ]]; then
    mv -n "${d}" "${node_cache}/" || true
  fi
done

# Move legacy tmp/ into excalidraw/ (best-effort; kept, not deleted)
if [[ -d "${root}/tmp" ]]; then
  tmp_dst="${excalidraw_root}/_tmp"
  mkdir -p "${tmp_dst}"
  if compgen -G "${root}/tmp/*" >/dev/null 2>&1; then
    find "${root}/tmp" -mindepth 1 -maxdepth 1 -exec mv -n {} "${tmp_dst}/" \;
  fi
  rmdir "${root}/tmp" 2>/dev/null || true
fi

echo "ok: migrated ${root}"
