---
name: seer
description: Visual feedback capture for any running macOS app window via osascript + screencapture. Use when the user wants UI verification or a fresh screenshot.
---

# Seer

## Overview
Capture a precise screenshot of a visible app window, annotate it for quick UI mockups, then compare against baselines to keep visual state in the agent loop.

## Quick start
1. Ensure the target app is running and Screen Recording + Accessibility are enabled for your terminal. (Automation -> System Events required for typing.)
2. Run the script:
   - `bash scripts/capture_app_window.sh` (defaults to frontmost app, output `.seer/captures/app-window-<app>-YYYYMMDD-HHMMSS-<pid>-<rand>.png`)
   - `bash scripts/capture_app_window.sh /path/to/out.png "Promptlight"` (custom output + process name)
3. (Optional) Create a mockup with annotations:
   - `bash scripts/mockup_ui.sh --spec spec.json`
   - `bash scripts/mockup_ui.sh --spec spec.json --json`
4. (Optional) Store + compare in the visual loop:
   - `bash scripts/loop_compare.sh /path/to/out.png web-home`
   - First run creates a baseline under `$SEER_LOOP_DIR` (default `.seer`)
5. Attach the current image (and diff image, if generated) with `view_image`.

## Usage
- `bash scripts/capture_app_window.sh --help`
- `bash scripts/capture_app_window.sh [out_path] [process_name]`
  - `out_path` default `.seer/captures/app-window-<app>-YYYYMMDD-HHMMSS-<pid>-<rand>.png`
  - `process_name` default frontmost app
  - set `SEER_OUT_DIR` to change default output root (falls back to `SEER_TMP_DIR` for legacy behavior)
- `bash scripts/type_into_app.sh --help`
- `bash scripts/type_into_app.sh --app "Promptlight" --text "hello" --enter`
- `bash scripts/type_into_app.sh --app "Promptlight" --click-rel 120,180 --text "hello"`
- `bash scripts/type_into_app.sh --text "hello" --no-activate`
- `bash scripts/mockup_ui.sh --help`
- `bash scripts/mockup_ui.sh --spec spec.json`
- `bash scripts/mockup_ui.sh --spec spec.json --json`
- `python3 scripts/annotate_image.py input.png output.png --spec spec.json`
- `annotate_image.py` supports top-level `defaults` (e.g., `auto_scale`, `outline`, `text_bg`), `spotlight` annotations to dim the background, and `fit` to auto-adjust rect/spotlight bounds.
- `bash scripts/loop_compare.sh --help`
- `bash scripts/loop_compare.sh [--loop-dir <path>] [--resize] [--update-baseline] <current_path> <baseline_name>`
  - set `SEER_LOOP_DIR` to change default loop directory (default `.seer`)
  - consider adding `.seer/` to `.gitignore`

## Workflow
1. **Capture**
   - `scripts/capture_app_window.sh`
   - If it fails, rerun with explicit process name or verify permissions.
2. **Compare (optional)**
   - `scripts/loop_compare.sh <current_path> <baseline_name>`
   - Stores `baselines/`, `latest/`, `history/`, `diffs/`, `reports/` under `.seer` (or `$SEER_LOOP_DIR`)
3. **Inspect**
   - Use `view_image` to load the current image and diff image.
4. **Iterate**
   - Repeat after UI changes or window repositioning.

## Resources
### scripts/
- `capture_app_window.sh`: grabs window bounds via System Events and runs `screencapture -x -R`.
- `type_into_app.sh`: focuses app and types text via System Events keystrokes.
- `annotate_image.py`: draws arrows, rectangles, and text on an image (requires `python3 -m pip install pillow`).
- `mockup_ui.sh`: capture window (optional) then annotate using a JSON spec.
- `compare_images.py`: compares baseline vs current and emits diff metrics + optional diff image (requires `python3 -m pip install pillow`).
- `loop_compare.sh`: manages baselines, history, and diff outputs for visual regression loops.

## Output layout (default)
Under `.seer/`:
- `captures/` capture images
- `mockups/` annotated mockups
- `specs/` JSON specs (same base name as mockup)
- `reports/` metadata JSON for each mockup
- `latest/` latest capture/mockup/spec per app slug
