---
name: seer
description: Visual feedback capture for any running macOS app window via osascript + screencapture. Use when the user wants UI verification or a fresh screenshot.
---

# Seer

## Overview
Grab a precise screenshot of a visible app window and attach it for visual iteration.

## Quick start
1. Ensure the target app is running and Screen Recording + Accessibility are enabled for your terminal.
2. Run the script:
   - `scripts/capture_app_window.sh` (defaults to frontmost app, output `/tmp/app-window-shot-YYYYMMDD-HHMMSS-<pid>-<rand>.png`)
   - `scripts/capture_app_window.sh /path/to/out.png "Promptlight"` (custom output + process name)
3. Attach the image with `view_image`.

## Usage
- `scripts/capture_app_window.sh --help`
- `scripts/capture_app_window.sh [out_path] [process_name]`
  - `out_path` default `/tmp/app-window-shot-YYYYMMDD-HHMMSS-<pid>-<rand>.png`
  - `process_name` default frontmost app

## Workflow
1. **Capture**
   - `scripts/capture_app_window.sh`
   - If it fails, rerun with explicit process name or verify permissions.
2. **Inspect**
   - Use `view_image` to load the output.
3. **Iterate**
   - Repeat after UI changes or window repositioning.

## Resources
### scripts/
- `capture_app_window.sh`: grabs window bounds via System Events and runs `screencapture -x -R`.
