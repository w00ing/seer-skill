---
name: seer
description: Captures visible macOS app windows and verifies UI changes against explicitly approved baselines. Use when an agent must inspect a fresh screenshot, verify native UI changes, or produce local visual QA evidence.
license: MIT
---

# Seer

Use the unified CLI for visual evidence. Keep specialist scripts for recording, annotation, wireframing, and typing. Seer requires macOS with Screen Recording and Accessibility permissions.

## Core workflow

1. Run `python3 scripts/seer doctor --json` to inspect platform, visible-window Accessibility readiness, and dependencies. Read `frontmost_process` separately from `capabilities.window_query.authorized`; a frontmost app does not prove accessible window enumeration. Screen Recording permission is checked during capture.
2. Run `python3 scripts/seer windows --json` and select the exact window's `window_id`.
3. Run `python3 scripts/seer capture --window-id 12345 --out .seer/capture/current.png --json`. Capture validates a fresh temporary PNG with the active `python3` and Pillow before atomically replacing the output. A failure leaves an existing output unchanged.
4. Load the returned `artifacts.current` path with `view_image`. Inspect the fresh image before making claims.
5. Run `python3 scripts/seer verify .seer/capture/current.png <baseline-name> --json`.
6. Load the returned diff image when status is `fail`, then iterate and capture again.

Never create or replace a baseline without explicit user approval. A missing baseline returns `needs_baseline` (exit 3); after approval, rerun with `--create-baseline`. Treat `--update-baseline` as a destructive approval action.

## CLI interface

```text
python3 scripts/seer doctor --json
python3 scripts/seer windows --json
python3 scripts/seer capture [--window-id ID|--process NAME] [--out PATH] --json
python3 scripts/seer verify [--loop-dir DIR] [--resize] [--max-diff-percent N]
                            [--create-baseline|--update-baseline]
                            CURRENT BASELINE --json
```

Commands emit one JSON object to stdout and diagnostics to stderr, except `--help`, which prints ordinary help text. Operational errors return exit 2 with `schema_version: 1`, `operation` (the recognized subcommand or `null`), `status: "error"`, and `error: {code, message}`. Stable error codes are `invalid_arguments`, `platform_unsupported`, `dependency_missing`, `subprocess_failed`, `invalid_subprocess_output`, `accessibility_required`, `filesystem_error`, and `not_ready`. Doctor errors also include `capabilities` and `frontmost_process`. Exit 0 means pass, 1 means visual fail, 2 means operational error, and 3 means `needs_baseline`. The default allowed difference is 0%.

`window_id` is the native, session-scoped identifier for one exact window. It survives movement and reordering, but becomes stale when the window closes or is recreated; rerun `windows` before retrying. The 1-based `index` remains informational. `capture --process` remains a compatibility fallback that targets the process's first window.

Exact-ID capture confirms that the window is on-screen before and after capture; hidden, minimized, or closed windows return an error. Inspect the fresh image even after PNG validation succeeds: a valid PNG does not establish that the UI content is correct.

Set `SEER_OUT_DIR` to change `.seer/` output or `SEER_LOOP_DIR` to change only baseline, latest, history, diff, and report storage.

## Optional workflows

- Record a window: `bash scripts/record_app_window.sh --duration 3 --summary --summary-sheet --summary-gif`
- Record a display or region: `bash scripts/record_screen.sh --help`
- Summarize video: `bash scripts/summarize_video.sh <video.mov> --mode scene --sheet --gif`
- Annotate a screenshot: `bash scripts/mockup_ui.sh --spec spec.json --json`
- Generate an Excalidraw scene: `python3 scripts/excalidraw_from_text.py --help`
- Test Excalidraw generation: `python3 scripts/test_excalidraw.py`
- Type into an app: inspect `bash scripts/type_into_app.sh --help`, then invoke only after explicit approval because it changes app state.

Use `--help` on specialist scripts for their complete options. Pillow is required for capture validation, image diff, and annotation; ffmpeg and ffprobe are optional for video workflows.

## Resources

- `scripts/seer`: machine-readable entry point; delegates capture and verification to existing scripts.
- `scripts/capture_app_window.sh`: captures an exact window ID or the first window of a process.
- `scripts/loop_compare.sh` and `scripts/compare_images.py`: manage approved baselines and exact pixel comparisons.
- `scripts/record_app_window.sh`, `scripts/record_screen.sh`, `scripts/summarize_video.sh`: video evidence.
- `scripts/mockup_ui.sh`, `scripts/annotate_image.py`: annotations.
- `scripts/excalidraw_from_text.py`: local wireframes.
- `scripts/test_excalidraw.py`: dependency-free Excalidraw regression checks.
- `scripts/type_into_app.sh`: explicit state-changing typing.
