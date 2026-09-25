---
name: seer
description: Captures visible macOS app windows and verifies UI changes against explicitly approved baselines. Use when an agent must inspect a fresh screenshot, verify native UI changes, or produce local visual QA evidence.
license: MIT
---

# Seer

Use the unified CLI for visual evidence. Keep specialist scripts for recording, annotation, wireframing, and typing. Seer requires macOS with Screen Recording and Accessibility permissions. Semantic queries are optional; a first query may compile a Swift helper and requires `swiftc` from Xcode Command Line Tools.

## Core workflow

1. Run `python3 scripts/seer doctor --json` to inspect platform, visible-window Accessibility readiness, and dependencies. Read `frontmost_process` separately from `capabilities.window_query.authorized`; a frontmost app does not prove accessible window enumeration. Screen Recording permission is checked during capture.
2. Run `python3 scripts/seer windows --json` and select the exact window's `window_id`.
3. When the UI is still animating or settling, run `python3 scripts/seer wait --stable --window-id 12345 --timeout 10 --interval 0.25 --stable-for 1 --max-diff-percent 0 --out .seer/capture/current.png --json`. This samples the same exact window and publishes the output only after at least two frames meet the pixel threshold for the stable interval. Timeout returns `fail` (exit 1) with reason `timeout`; capture or input errors return `error` (exit 2). A stable result establishes a pixel condition, not semantic UI correctness. For an immediate capture, use `capture --window-id 12345 --out .seer/capture/current.png --json`.
4. Load the returned `artifacts.current` path with `view_image`. Inspect the fresh image before making claims. Capture writes a hash-bound `.seer.json` sidecar with available capture time, window ID, PNG dimensions, and DPI metadata; its path is `artifacts.metadata`. Unknown, missing, or stale metadata is not inferred.
5. Run `python3 scripts/seer verify .seer/capture/current.png <baseline-name> --json`. Add repeated `--ignore-rect X,Y,WIDTH,HEIGHT` options only for known dynamic regions. Rectangles use nonnegative integer baseline PNG coordinates, positive dimensions, half-open edges, and must be fully in bounds. Overlapping areas count once; excluded pixels leave both the changed-pixel numerator and denominator. Invalid rectangles and a full-image exclusion are errors.
6. Inspect the returned diff when status is `fail`, then iterate and capture again. A valid comparison or baseline creation stores a per-run bundle under `runs/<unique>/` with baseline/current snapshots, report, manifest, and a diff when pixel comparison succeeds. The manifest records relative artifact paths, checksums, capture metadata, and comparison options. Dimension or scale mismatch attempts preserve the snapshots, report, and manifest but have no diff. A missing baseline or invalid input that stops before comparison setup leaves the loop directory untouched. The snapshot preserves the baseline used for that comparison, including when `--update-baseline` is approved.

Never create or replace a baseline without explicit user approval. A missing baseline returns `needs_baseline` (exit 3); after approval, rerun with `--create-baseline`. Treat `--update-baseline` as a destructive approval action.

## Semantic inspection

Use `inspect`, `assert`, or a semantic `wait` when the question concerns exposed labels, values, roles, or enabled state. Accessibility is the default source; select `--source ocr` explicitly to inspect text in the window pixels.

```bash
python3 scripts/seer inspect --window-id 12345 --source ax --json
python3 scripts/seer assert --window-id 12345 --text "Saved" --match exact --json
python3 scripts/seer assert --window-id 12345 --enabled "Save" --role AXButton --json
python3 scripts/seer assert --window-id 12345 --source ocr --text "Saved" \
  --min-confidence 0.8 --timeout 60 --json
python3 scripts/seer wait --text "Ready" --window-id 12345 \
  --timeout 10 --interval 0.25 --json
```

`inspect` and `assert` allow 30 seconds by default; semantic `wait` allows 10. The query budget includes helper compilation, capture, and OCR work. Use a longer timeout such as `--timeout 60` for OCR or a cold helper build.

`inspect` returns Accessibility role, name, value, bounds, and enabled state; its OCR source returns recognized text with confidence and bounds. `assert` supports `--text`, `--text-absent`, `--enabled`, and `--disabled`. Text checks compare case-sensitively against individual Accessibility name/value fields. `--match` defaults to `contains` and also accepts `exact`; control names match exactly. Use `--role` to restrict an enabled/disabled query to that exact Accessibility role. Semantic `wait` accepts one of those conditions or the existing `--stable` pixel condition.

Queries never fall back between Accessibility and OCR. `--region X,Y,WIDTH,HEIGHT` uses window-local points from the top-left. Accessibility selects elements whose bounds centers are in the region; OCR crops the corresponding image region and translates its recognized bounds back to window coordinates. Visual `--ignore-rect` options remain measured in baseline PNG pixels.

An `inspect` result with exit 0 means the read completed; check `complete` and `issues` before using its evidence. `assert` and semantic `wait` turn incomplete observations into errors. Named static text and unknown roles cannot establish a control's enabled state. Inspection reports are saved locally; save assertion/wait stdout to preserve the verdict. Semantic verdicts do not use visual comparison replay. Vision can misread text even at confidence 1.0; inspect recognized text before relying on it.

Treat missing evidence as error, never as proof of absence. Empty or incomplete Accessibility text is insufficient; complete-tree absence applies only to the app's exposed Accessibility tree. OCR can establish a positive text match above `--min-confidence`, but low-confidence evidence is insufficient. OCR cannot prove text absence and cannot check control state. A valid mismatch returns exit 1, operational or insufficient-evidence errors return exit 2, and a wait whose valid condition remains unmet at its deadline returns a timeout failure. Inspect the returned source/confidence evidence. The [agent workflow](../../docs/seer-agent-loop.md) describes these evidence limits and Apple API boundaries.

## CLI interface

```text
python3 scripts/seer doctor --json
python3 scripts/seer windows --json
python3 scripts/seer capture [--window-id ID|--process NAME] [--out PATH] --json
python3 scripts/seer inspect --window-id ID [--source ax|ocr]
                           [--region X,Y,WIDTH,HEIGHT] [--timeout SEC] --json
python3 scripts/seer assert --window-id ID [--source ax|ocr]
                           (--text TEXT|--text-absent TEXT|--enabled NAME|--disabled NAME)
                           [--role AXButton] [--match exact|contains]
                           [--min-confidence N] [--region X,Y,WIDTH,HEIGHT]
                           [--timeout SEC] --json
python3 scripts/seer wait --stable --window-id ID [--timeout SEC] [--interval SEC]
                          [--stable-for SEC] [--max-diff-percent N]
                          [--ignore-rect X,Y,WIDTH,HEIGHT ...] [--out PATH] --json
python3 scripts/seer wait (--stable|--text TEXT|--text-absent TEXT|--enabled NAME|--disabled NAME)
                          --window-id ID [--source ax|ocr] [--role AXButton]
                          [--match exact|contains] [--min-confidence N]
                          [--region X,Y,WIDTH,HEIGHT] [--timeout SEC]
                          [--interval SEC] --json
python3 scripts/seer verify [--loop-dir DIR] [--resize] [--max-diff-percent N]
                            [--ignore-rect X,Y,WIDTH,HEIGHT ...]
                            [--create-baseline|--update-baseline]
                            CURRENT BASELINE --json
```

Commands emit one JSON object to stdout and diagnostics to stderr, except `--help`, which prints ordinary help text. Operational errors return exit 2 with `schema_version: 1`, `operation` (the recognized subcommand or `null`), `status: "error"`, and `error: {code, message}`. Doctor errors also include `capabilities` and `frontmost_process`. Exit 0 means pass, exit 1 means a valid visual or semantic condition failed (including a condition wait timeout), exit 2 means an operational error or insufficient semantic evidence, and exit 3 means `needs_baseline`. The default allowed visual difference is 0%.

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
