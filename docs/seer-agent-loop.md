# Seer evidence workflow

Seer v0.6 provides a local, machine-readable loop for checking what a native macOS app displayed. It captures and reports visible pixels; app interaction stays with the agent or a separate UI automation layer.

## Implemented workflow

1. Run `skills/seer/scripts/seer doctor --json` to check platform, visible-window Accessibility readiness, Pillow, and optional video tools. Read `frontmost_process` separately from `capabilities.window_query.authorized`; Screen Recording permission is checked during capture.
2. Run `skills/seer/scripts/seer windows --json`, then select a returned `window_id`. The native identifier addresses one exact visible window and remains valid when it moves or changes order. It expires when that window closes or is recreated; `index` is only informational.
3. If the app is animating, wait for a stable capture with `skills/seer/scripts/seer wait --stable --window-id <id> --timeout 10 --interval 0.25 --stable-for 1 --max-diff-percent 0 --out .seer/capture/current.png --json`. The command compares at least two frames from the same ID against a stable-interval anchor. A change resets the interval. Timeout covers capture subprocesses and polling, returns `fail` (exit 1, reason `timeout`), and leaves any existing output untouched. Capture or invalid-input errors return `error` (exit 2). A successful result has `reason: "stable"`; `condition` reports `stable_for`, `interval`, `timeout`, `max_diff_percent`, `ignore_rects`, and `reference: "interval_anchor"`. Capture provenance is nested under `capture` and uses `source: "seer.capture"`; image and sidecar paths are under `artifacts`. A stable result is a pixel condition, not proof of correct labels, controls, or behavior. For immediate capture, use `capture --window-id <id> --out .seer/capture/current.png --json`.
4. Inspect the returned `artifacts.current` image before making claims. Capture writes a hash-bound `.seer.json` sidecar containing available capture time, window ID, image dimensions, and DPI metadata; `artifacts.metadata` contains its path. External, missing, or stale sidecar data is not invented; unknown values remain null.
5. Compare against an explicitly approved baseline with `skills/seer/scripts/seer verify .seer/capture/current.png <name> --json`. Add repeated `--ignore-rect X,Y,WIDTH,HEIGHT` options for known dynamic regions. Coordinates refer to baseline PNG pixels. Rectangles have half-open bounds, must be fully in bounds, and overlapping areas count once. Ignored pixels are excluded from both changed-pixel count and denominator. A malformed rectangle or mask covering the full image is an error.
6. Inspect the report and diff when the result is `fail`, make a code change through the appropriate development workflow, then capture and compare again. A comparison with valid baseline/current images preserves the baseline snapshot, current image, report, manifest, and options in an immutable bundle under `.seer/loop/runs/<unique>/`, with relative artifact paths and checksums. A diff is included when pixel comparison succeeds; a dimension or scale mismatch still has the snapshots and report but no diff. Missing baselines and invalid inputs rejected before comparison setup leave the loop directory untouched. An approved `--update-baseline` does not change the baseline snapshot in the run bundle.

## Interface and evidence boundaries

The CLI prints one JSON object for command results and operational errors; diagnostics go to stderr. Operational errors use `schema_version: 1`, `status: "error"`, an operation name (or `null`), and an `error` object with a stable code and message. `--help` prints ordinary help text. Comparison errors include `image_size_mismatch` and `image_scale_mismatch`; direct comparator errors also include `image_not_found` and `invalid_image`.

PNG dimensions and known DPI values are reported. Seer never resizes implicitly; `verify --resize` explicitly opts into the baseline pixel grid, and `scale_evidence` records that choice. A known DPI mismatch without the flag returns `image_scale_mismatch`; a dimension mismatch returns `image_size_mismatch`. Unknown DPI or scale is not treated as a verified scale match. Screen Recording and Accessibility are separate macOS permissions. A process can be frontmost while window enumeration is unavailable. A failed doctor probe does not distinguish permission denial from the absence of an accessible visible window.

- A successful command-stub check proves the CLI handles controlled subprocess responses. It does not prove macOS permissions are granted, a real window is captured correctly, or pixels match the user's expectation.
- Pixel comparison establishes measured image similarity only. It does not confirm application semantics or user intent.
- Keep captures, diffs, reports, and run bundles local under `.seer/`; version baselines only when the project explicitly intends to retain them.
- Never create or replace a real baseline without explicit user approval.

<a id="later-directions"></a>

## Roadmap

- v0.7: Accessibility-based UI assertions and optional OCR.
- v0.8: a thin local MCP adapter and evaluated agent workflows.
- v0.9: installation, compatibility, and release hardening based on actual use.

For the v0.6 test checklist and evidence status, see [v0.6 validation](v0.6-validation.md). The prior release's record is [v0.5 validation](v0.5-validation.md). Pixel metrics and artifact details are described in [Visual loop](visual-loop.md).
