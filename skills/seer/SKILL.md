---
name: seer
description: Captures visible macOS app windows and verifies UI changes against explicitly approved baselines. Use when an agent must inspect a fresh screenshot, verify native UI changes, or produce local visual QA evidence.
license: MIT
---

# Seer

Use the unified CLI for visual evidence. Keep specialist scripts for recording, annotation, wireframing, and typing. Seer requires macOS with Screen Recording and Accessibility permissions.

## Core workflow

1. Run `python3 scripts/seer doctor --json`. Resolve missing permissions or dependencies before capture.
2. Run `python3 scripts/seer windows --json` and select the exact process name.
3. Run `python3 scripts/seer capture --process "AppName" --out .seer/capture/current.png --json`.
4. Load the returned `artifacts.current` path with `view_image`. Inspect the fresh image before making claims.
5. Run `python3 scripts/seer verify .seer/capture/current.png <baseline-name> --json`.
6. Load the returned diff image when status is `fail`, then iterate and capture again.

Never create or replace a baseline without explicit user approval. A missing baseline returns `needs_baseline` (exit 3); after approval, rerun with `--create-baseline`. Treat `--update-baseline` as a destructive approval action.

## CLI interface

```text
python3 scripts/seer doctor --json
python3 scripts/seer windows --json
python3 scripts/seer capture [--process NAME] [--out PATH] --json
python3 scripts/seer verify [--loop-dir DIR] [--resize] [--max-diff-percent N]
                            [--create-baseline|--update-baseline]
                            CURRENT BASELINE --json
```

Commands emit at most one JSON object to stdout and diagnostics to stderr. Exit 0 means pass, 1 means visual fail, 2 means tool/input error, and 3 means `needs_baseline`. The default allowed difference is 0%.

`windows` indexes are informational and unstable. `capture` currently targets the selected process's first window; rerun it after window movement or state changes.

Set `SEER_OUT_DIR` to change `.seer/` output or `SEER_LOOP_DIR` to change only baseline, latest, history, diff, and report storage.

## Optional workflows

- Record a window: `bash scripts/record_app_window.sh --duration 3 --summary --summary-sheet --summary-gif`
- Record a display or region: `bash scripts/record_screen.sh --help`
- Summarize video: `bash scripts/summarize_video.sh <video.mov> --mode scene --sheet --gif`
- Annotate a screenshot: `bash scripts/mockup_ui.sh --spec spec.json --json`
- Generate an Excalidraw scene: `python3 scripts/excalidraw_from_text.py --help`
- Test Excalidraw generation: `python3 scripts/test_excalidraw.py`
- Type into an app: inspect `bash scripts/type_into_app.sh --help`, then invoke only after explicit approval because it changes app state.

Use `--help` on specialist scripts for their complete options. Pillow is required for image diff and annotation; ffmpeg and ffprobe are optional for video workflows.

## Resources

- `scripts/seer`: machine-readable entry point; delegates capture and verification to existing scripts.
- `scripts/capture_app_window.sh`: captures the first window of a process.
- `scripts/loop_compare.sh` and `scripts/compare_images.py`: manage approved baselines and exact pixel comparisons.
- `scripts/record_app_window.sh`, `scripts/record_screen.sh`, `scripts/summarize_video.sh`: video evidence.
- `scripts/mockup_ui.sh`, `scripts/annotate_image.py`: annotations.
- `scripts/excalidraw_from_text.py`: local wireframes.
- `scripts/test_excalidraw.py`: dependency-free Excalidraw regression checks.
- `scripts/type_into_app.sh`: explicit state-changing typing.
