# Visual loop

Seer compares a current screenshot with an explicitly approved baseline and records pixel metrics, a diff image, and a JSON report under `.seer/loop/` (or `$SEER_LOOP_DIR`). The default allowed difference is 0%.

## Capture and compare

```bash
SEER=skills/seer/scripts/seer

"$SEER" doctor --json
"$SEER" windows --json
"$SEER" capture --window-id 12345 --out .seer/capture/current.png --json
# Inspect .seer/capture/current.png before interpreting the result.
"$SEER" verify .seer/capture/current.png settings --json
```

`windows` returns a native session-scoped `window_id`; it selects an exact window and expires if that window is closed or recreated. Capture validates the screenshot as a fresh PNG using Pillow in the active `python3` environment. It replaces the requested output atomically only after validation succeeds, preserving any existing output on failure.

`verify` stores the current image, diff, comparison history, and report in the loop directory. Use `--max-diff-percent N` to allow a deliberate amount of changed pixels. The lower-level `compare_images.py` command is also available when a standalone pixel comparison is useful:

```bash
python3 skills/seer/scripts/compare_images.py baseline.png current.png \
  --diff-out .seer/loop/diffs/home.png \
  --json-out .seer/loop/reports/home.json

# Optional: resize current to the baseline dimensions before comparing.
python3 skills/seer/scripts/compare_images.py baseline.png current.png --resize
```

`compare_images.py` reports:

- `percent_changed`: share of pixels that differ.
- `avg_diff_percent`: average per-pixel difference intensity.

## Results and errors

The unified CLI returns `pass` with exit 0 when the difference is within the threshold, `fail` with exit 1 when it exceeds the threshold, `error` with exit 2 for an operational failure, and `needs_baseline` with exit 3 when the named baseline is absent. Missing baselines are not created implicitly. Ask for explicit approval before creating or replacing a baseline.

Operational errors produce one JSON object on stdout with `schema_version: 1`, `operation` (the recognized subcommand or `null`), `status: "error"`, and `error: {code, message}`; human-readable diagnostics are sent to stderr. Doctor errors also carry the capability report and `frontmost_process`. The `--help` forms print ordinary help text. See [v0.5 validation](v0.5-validation.md) for automated-versus-real-app validation boundaries.
