# Visual Loop: compare_images.py

## Goal
Keep visual state in the agent loop by comparing a current screenshot against a baseline and producing a diff image + metrics.

## How it works
- `compare_images.py` loads `baseline` and `current` images with Pillow and compares pixels.
- It computes:
  - `percent_changed`: percent of pixels that differ.
  - `avg_diff_percent`: average per-pixel diff intensity.
- It can write a diff visualization (current image with red diff overlay) and a JSON report.
- If sizes differ, use `--resize` to resize current to the baseline size.

## Usage
```bash
python3 scripts/compare_images.py baseline.png current.png \
  --diff-out .seer/diffs/home-20251221.png \
  --json-out .seer/reports/home-20251221.json

# If sizes differ
python3 scripts/compare_images.py baseline.png current.png --resize
```

## Loop integration
`loop_compare.sh` wraps `compare_images.py` and manages baselines, latest, history, diffs, and reports under `.seer/` (or `$SEER_LOOP_DIR`).
