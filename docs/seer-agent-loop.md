# Seer evidence workflow

Seer v0.5 provides a local, machine-readable loop for checking what a native macOS app actually displayed. It captures and reports evidence; app interaction stays with the agent or a separate UI automation layer.

## Implemented workflow

1. Run `skills/seer/scripts/seer doctor --json` to check platform, visible-window Accessibility readiness, Pillow, and optional video tools. Read `frontmost_process` separately from `capabilities.window_query.authorized`; Screen Recording permission is checked during capture.
2. Run `skills/seer/scripts/seer windows --json`, then select a returned `window_id`. The native identifier addresses one exact visible window and remains valid when it moves or changes order. It expires when that window closes or is recreated; `index` is only informational.
3. Capture that exact window with `skills/seer/scripts/seer capture --window-id <id> --out .seer/capture/current.png --json`. Capture validates a fresh temporary PNG with the active `python3` and Pillow, then atomically replaces the output only after validation succeeds.
4. Inspect the returned `artifacts.current` image. A capture is evidence of visible pixels, not a claim that labels, controls, or application behavior are correct.
5. Compare against an explicitly approved baseline with `skills/seer/scripts/seer verify .seer/capture/current.png <name> --json`. If the baseline is missing, Seer reports `needs_baseline` (exit 3) and leaves it absent. Ask for approval before creating or replacing any real baseline.
6. Inspect the report and diff when the result is `fail`, make a code change through the appropriate development workflow, then capture and compare again.

The CLI prints one JSON object for command results and operational errors; diagnostics go to stderr. Operational errors use `schema_version: 1`, `status: "error"`, an operation name (or `null`), and an `error` object with a stable code and message. `--help` prints ordinary help text. Successful doctor output keeps `frontmost_process` separate from Accessibility status under `capabilities.window_query.authorized`.

## Evidence boundaries

- Screen Recording and Accessibility are separate macOS permissions. A process can be frontmost while window enumeration is unavailable. A failed doctor window probe does not distinguish permission denial from the absence of an accessible visible window.
- A successful command-stub test proves the CLI handles controlled subprocess responses. It does not prove that macOS permissions are granted, a real window is captured correctly, or pixels match the user's expectation.
- Keep captures, diffs, and reports local under `.seer/`; version baselines only when the project explicitly intends to retain them.
- Do not create or replace a real baseline without explicit user approval.

## Later directions

Planned directions, not implemented in v0.5:

- v0.6: comparison masks, stable-frame detection, and reproducible evidence bundles.
- v0.7: Accessibility-based assertions and optional OCR.
- v0.8: a thin local MCP adapter and evaluated agent workflows.
- v0.9: installation, compatibility, and release hardening based on actual use.

These should preserve explicit baseline approval and local evidence storage.

For representative checks and recorded results, see [v0.5 validation](v0.5-validation.md). Pixel metrics and artifact details are described in [Visual loop](visual-loop.md).
