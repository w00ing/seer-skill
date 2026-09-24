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
3. When the UI is still animating or settling, run `python3 scripts/seer wait --stable --window-id 12345 --timeout 10 --interval 0.25 --stable-for 1 --max-diff-percent 0 --out .seer/capture/current.png --json`. This samples the same exact window and publishes the output only after at least two frames meet the pixel threshold for the stable interval. Timeout returns `fail` (exit 1) with reason `timeout`; capture or input errors return `error` (exit 2). A stable result establishes a pixel condition, not semantic UI correctness. For an immediate capture, use `capture --window-id 12345 --out .seer/capture/current.png --json`.
4. Load the returned `artifacts.current` path with `view_image`. Inspect the fresh image before making claims. Capture writes a hash-bound `.seer.json` sidecar with available capture time, window ID, PNG dimensions, and DPI metadata; its path is `artifacts.metadata`. Unknown, missing, or stale metadata is not inferred.
5. Run `python3 scripts/seer verify .seer/capture/current.png <baseline-name> --json`. Add repeated `--ignore-rect X,Y,WIDTH,HEIGHT` options only for known dynamic regions. Rectangles use nonnegative integer baseline PNG coordinates, positive dimensions, half-open edges, and must be fully in bounds. Overlapping areas count once; excluded pixels leave both the changed-pixel numerator and denominator. Invalid rectangles and a full-image exclusion are errors.
6. Inspect the returned diff when status is `fail`, then iterate and capture again. A valid comparison or baseline creation stores a per-run bundle under `runs/<unique>/` with baseline/current snapshots, report, manifest, and a diff when pixel comparison succeeds. The manifest records relative artifact paths, checksums, capture metadata, and comparison options. Dimension or scale mismatch attempts preserve the snapshots, report, and manifest but have no diff. A missing baseline or invalid input that stops before comparison setup leaves the loop directory untouched. The snapshot preserves the baseline used for that comparison, including when `--update-baseline` is approved.

Never create or replace a baseline without explicit user approval. A missing baseline returns `needs_baseline` (exit 3); after approval, rerun with `--create-baseline`. Treat `--update-baseline` as a destructive approval action.

## CLI interface

```text
python3 scripts/seer doctor --json
python3 scripts/seer windows --json
python3 scripts/seer capture [--window-id ID|--process NAME] [--out PATH] --json
python3 scripts/seer wait --stable --window-id ID [--timeout SEC] [--interval SEC]
                          [--stable-for SEC] [--max-diff-percent N]
                          [--ignore-rect X,Y,WIDTH,HEIGHT ...] [--out PATH] --json
python3 scripts/seer verify [--loop-dir DIR] [--resize] [--max-diff-percent N]
                            [--ignore-rect X,Y,WIDTH,HEIGHT ...]
                            [--create-baseline|--update-baseline]
                            CURRENT BASELINE --json
```

Commands emit one JSON object to stdout and diagnostics to stderr, except `--help`, which prints ordinary help text. Operational errors return exit 2 with `schema_version: 1`, `operation` (the recognized subcommand or `null`), `status: "error"`, and `error: {code, message}`. Stable error codes are `invalid_arguments`, `platform_unsupported`, `dependency_missing`, `subprocess_failed`, `invalid_subprocess_output`, `accessibility_required`, `filesystem_error`, and `not_ready`. Doctor errors also include `capabilities` and `frontmost_process`. Exit 0 means pass, 1 means visual fail, 2 means operational error, and 3 means `needs_baseline`. The default allowed difference is 0%.

`window_id` is the native, session-scoped identifier for one exact window. It survives movement and reordering, but becomes stale when the window closes or is recreated; rerun `windows` before retrying. The 1-based `index` remains informational. `capture --process` remains a compatibility fallback that targets the process's first window.

Comparison reports PNG dimension and known DPI mismatches. It never resizes implicitly. `verify --resize` explicitly opts into the baseline pixel grid; the current image is resized when pixel dimensions differ, and `scale_evidence` records the opt-in. A known DPI mismatch without that flag returns `image_scale_mismatch`; a dimension mismatch returns `image_size_mismatch`. Unknown scale is not treated as a verified match. `image_not_found` and `invalid_image` are image-comparator error codes. The `wait` timeout covers capture subprocesses as well as frame polling. A timeout does not replace an existing `--out` file. A successful wait response has `reason: "stable"`, a `condition` object (`stable_for`, `interval`, `timeout`, `max_diff_percent`, `ignore_rects`, and `reference: "interval_anchor"`), capture metadata under `capture` (`source: "seer.capture"`), and image/metadata paths under `artifacts`.

Exact-ID capture confirms that the window is on-screen before and after capture; hidden, minimized, or closed windows return an error. Inspect the fresh image even after PNG validation succeeds: a valid PNG does not establish that the UI content is correct.

Set `SEER_OUT_DIR` to change `.seer/` output or `SEER_LOOP_DIR` to change only baseline, latest, history, diff, report, and evidence-bundle storage.

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
