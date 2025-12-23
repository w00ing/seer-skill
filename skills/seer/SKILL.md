---
name: seer
description: Visual feedback capture for any running macOS app window via osascript + screencapture. Use when the user wants UI verification or a fresh screenshot.
---

# Seer

## Overview
Capture a precise screenshot of a visible app window, then compare against baselines to keep visual state in the agent loop.

## Quick start
1. Ensure the target app is running and Screen Recording + Accessibility are enabled for your terminal. (Automation -> System Events required for typing.)
2. Run the script:
   - `bash scripts/capture_app_window.sh` (defaults to frontmost app, output `/tmp/seer/app-window-shot-YYYYMMDD-HHMMSS-<pid>-<rand>.png`)
   - `bash scripts/capture_app_window.sh /path/to/out.png "Promptlight"` (custom output + process name)
3. (Optional) Store + compare in the visual loop:
   - `bash scripts/loop_compare.sh /path/to/out.png web-home`
   - First run creates a baseline under `$SEER_LOOP_DIR` (default `.seer`)
4. Attach the current image (and diff image, if generated) with `view_image`.

## Usage
- `bash scripts/capture_app_window.sh --help`
- `bash scripts/capture_app_window.sh [out_path] [process_name]`
  - `out_path` default `/tmp/seer/app-window-shot-YYYYMMDD-HHMMSS-<pid>-<rand>.png`
  - `process_name` default frontmost app
  - set `SEER_TMP_DIR` to change default output dir
- `bash scripts/type_into_app.sh --help`
- `bash scripts/type_into_app.sh --app "Promptlight" --text "hello" --enter`
- `bash scripts/type_into_app.sh --app "Promptlight" --click-rel 120,180 --text "hello"`
- `bash scripts/type_into_app.sh --text "hello" --no-activate`
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
- `compare_images.py`: compares baseline vs current and emits diff metrics + optional diff image (requires `python3 -m pip install pillow`).
- `loop_compare.sh`: manages baselines, history, and diff outputs for visual regression loops.
