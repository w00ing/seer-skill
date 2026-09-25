# Visual loop

Seer compares a current screenshot with an explicitly approved baseline and records pixel metrics, a diff image, a JSON report, and a reproducible per-run evidence bundle under `.seer/loop/` (or `$SEER_LOOP_DIR`). The default allowed difference is 0%.

## Capture and compare

```bash
SEER=skills/seer/scripts/seer

"$SEER" doctor --json
"$SEER" windows --json
# Use the exact native window_id returned above.
"$SEER" wait --stable --window-id 12345 --timeout 10 --interval 0.25 \
  --stable-for 1 --max-diff-percent 0 --out .seer/capture/current.png --json
# Inspect .seer/capture/current.png before interpreting the result.
"$SEER" verify .seer/capture/current.png settings --json
```

`windows` returns a native session-scoped `window_id`; it selects one exact window and expires if that window is closed or recreated. `wait --stable` captures only that ID and requires at least two frames to meet the configured pixel threshold over the stable interval. Each frame is compared with the interval anchor; a change resets the interval. This prevents gradual pixel drift from adding up to a false stable result. Its timeout covers capture subprocesses as well as frame polling. On timeout, the command returns `fail` with reason `timeout` and leaves an existing `--out` untouched. A successful result reports `reason: "stable"`, the effective `condition` fields (`stable_for`, `interval`, `timeout`, `max_diff_percent`, `ignore_rects`, and `reference: "interval_anchor"`), and final capture metadata under `capture`. That metadata has `source: "seer.capture"`; the sidecar path appears in `artifacts.metadata`. Output is published only on success. A stable result proves only the measured pixel condition, not semantic correctness of the UI.

Capture validates a fresh PNG using Pillow in the active `python3` environment and writes a hash-bound `<image>.seer.json` sidecar for available capture time, window ID, image dimensions, and DPI metadata. The capture JSON returns the sidecar path as `artifacts.metadata`. Sidecar values are associated with the image hash. Missing or stale metadata is not guessed; unknown fields remain null.

`verify` stores the compared current image, diff, comparison history, report, and per-run bundle. Use `--max-diff-percent N` to allow a deliberate amount of changed pixels. The named baseline must be explicitly approved. A missing baseline returns `needs_baseline` (exit 3) without creating it; creating or updating a baseline requires approval.

Repeated `--ignore-rect X,Y,WIDTH,HEIGHT` options exclude known dynamic regions from both changed-pixel count and denominator. Coordinates are integer pixels in the baseline image, with half-open bounds `[X, X+WIDTH) × [Y, Y+HEIGHT)`. Each rectangle must have positive dimensions and lie fully inside the image. Overlap counts once. Invalid rectangles and excluding the full comparison image are errors. Seer does not infer masks.

PNG dimensions and known DPI values are checked and reported; images are never resized implicitly. Use `verify --resize` to explicitly opt into the baseline pixel grid. The current image is resampled when pixel dimensions differ; `scale_evidence` records the opt-in. With matching dimensions, a known DPI mismatch without the flag returns `image_scale_mismatch`; a pixel-dimension mismatch without it returns `image_size_mismatch`. An unknown DPI or scale is not treated as a verified scale match.

## Reproducible run evidence

Each comparison with a valid baseline/current image creates an immutable evidence bundle under `.seer/loop/runs/<unique>/`. The bundle contains the baseline snapshot, current image, report, and a manifest with relative artifact paths, checksums, capture metadata, and comparison options. A diff is included when pixel comparison succeeds; a dimension or scale mismatch keeps the snapshots and report but has no diff. Missing baselines and invalid inputs rejected before comparison setup leave the loop directory untouched. The bundle records which baseline was compared even when an explicitly approved `--update-baseline` replaces the active baseline. Preserve the bundle directory to replay or inspect that run; do not rely on mutable `latest/` paths for historical evidence.

To replay a recorded comparison, run the `replay.command` from the bundle's `manifest.json` in the bundle directory, where its `baseline.png` and `current.png` paths resolve. The command reuses the captured options and emits comparison metrics as JSON. If the recorded absolute path to `compare_images.py` is no longer valid, replace it with the installed Seer script path. The report also retains the original run's status and artifact paths; those path fields describe the original local run.

The standalone `compare_images.py` command remains available for a direct two-image pixel comparison:

```bash
python3 skills/seer/scripts/compare_images.py baseline.png current.png \
  --diff-out .seer/loop/diffs/home.png \
  --json-out .seer/loop/reports/home.json \
  --ignore-rect 0,0,240,64 --max-diff-percent 0.5

# Optional: resize current to baseline dimensions before comparing.
python3 skills/seer/scripts/compare_images.py baseline.png current.png --resize
```

Its `percent_changed` field is the share of compared pixels that differ, and `avg_diff_percent` is the average per-pixel difference intensity. `pixels_total` is the compared-pixel denominator, `pixels_excluded` is the number excluded by masks, and `pixels_image_total` is the full baseline area. `scale_evidence` records whether dimensions and known PNG DPI values matched and whether normalization was explicitly requested. The unified CLI adds approved-baseline management and per-run evidence storage. Direct image errors include `image_not_found` and `invalid_image`.

## Semantic queries

Seer 0.7 can query the selected window's Accessibility tree or, when explicitly requested, run OCR over its visible pixels:

```bash
skills/seer/scripts/seer inspect --window-id 12345 --source ax --json
skills/seer/scripts/seer assert --window-id 12345 --text "Saved" --match exact --json
skills/seer/scripts/seer assert --window-id 12345 --source ocr --text "Saved" \
  --min-confidence 0.8 --json
```

Accessibility is the default and reports exposed roles, names, values, bounds, and enabled state. Text assertions check individual name/value fields case-sensitively; controls match by exact name. OCR is opt-in and reports recognized text, confidence, and bounds. It can establish a sufficiently confident positive text match, but cannot establish control state or prove text absence. For assertions and semantic waits, empty, truncated, partial, timed-out, or weak evidence is an operational error (exit 2), not a passing absence check. An inspection can finish successfully with `complete: false`; inspect callers must check that field and `issues`. A valid mismatch is `fail` (exit 1). See [Seer agent workflow](seer-agent-loop.md) for wait conditions, region mapping, and the full evidence limits.

`--region` coordinates are window-local points from the top-left. Accessibility checks element-bound centers against this region; OCR crops the corresponding image area and maps text bounds back to the window. This coordinate system is separate from `--ignore-rect`, which uses baseline PNG pixels for image comparisons.

## Results and errors

The unified CLI returns `pass` with exit 0 when the comparison is within threshold, `fail` with exit 1 when it exceeds the threshold or stability times out, `error` with exit 2 for an operational failure, and `needs_baseline` with exit 3 when the named baseline is absent. Operational errors produce one JSON object on stdout with `schema_version: 1`, `operation` (recognized subcommand or `null`), `status: "error"`, and `error: {code, message}`; diagnostics go to stderr. Doctor errors also carry the capability report and `frontmost_process`. The `--help` forms print ordinary help text.

See [v0.7 validation](v0.7-validation.md) for check results and remaining limits. The [v0.6 validation page](v0.6-validation.md) is a historical record, and the [v0.5 validation record](v0.5-validation.md) documents the earlier release.
