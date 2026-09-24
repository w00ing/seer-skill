# Seer agent workflow

Seer 0.7 combines pixel evidence with explicit semantic queries for a native macOS window. It observes the UI; app interaction stays with the agent or a separate automation layer.

## Capture and verify pixels

1. Run `skills/seer/scripts/seer doctor --json`, then `windows --json`; select the exact returned `window_id`.
2. Capture the window, or use `wait --stable` while it is settling. Inspect the returned image with `view_image` before making visual claims.
3. Compare with `verify <current.png> <baseline> --json`. A missing baseline returns `needs_baseline`; create or replace one only after the user explicitly approves it.
4. Inspect the report and diff after a failed comparison, then repeat after changes. Pixel similarity and stable pixels do not establish semantic correctness.

## Inspect and assert UI semantics

Use Accessibility as the default source. It reads the app's exposed Accessibility tree and returns element roles, names, values, bounds, and enabled state. OCR is an opt-in text source over the captured window image; it does not reveal control state.

```bash
SEER=skills/seer/scripts/seer

# Read exposed roles, names, values, bounds, and enabled state.
"$SEER" inspect --window-id 12345 --source ax --json

# Match text case-sensitively within an individual AX name or value.
"$SEER" assert --window-id 12345 --text "Saved" --match exact --json

# Ask OCR to find text in the visible pixels and require a confidence threshold.
"$SEER" assert --window-id 12345 --source ocr --text "Saved" \
  --min-confidence 0.8 --json

# Wait until a semantic condition is observed, or wait for pixel stability.
"$SEER" wait --text "Ready" --window-id 12345 --timeout 10 --interval 0.25 --json
"$SEER" wait --stable --window-id 12345 --timeout 10 --interval 0.25 \
  --stable-for 1 --max-diff-percent 0 --json
```

`assert` supports `--text`, `--text-absent`, `--enabled`, and `--disabled`; add `--role AXButton` to restrict a control-state query to that exact role. Text matching is case-sensitive and `--match` is `contains` by default or `exact`. Control names match exactly. Semantic `wait` accepts those conditions or the existing `--stable` pixel condition.

Without `--source`, semantic commands query Accessibility. OCR is selected only with `--source ocr`; Seer never switches sources automatically. A region uses `X,Y,WIDTH,HEIGHT` in window-local points with a top-left origin. Accessibility filtering uses the centers of element bounds; OCR crops the corresponding image region and maps recognized bounds back to the window. `--ignore-rect` remains a visual-comparison option measured in baseline PNG pixels.

`inspect` returns exit 0 when a query completes, even if its `complete` field is false; inspect callers must check `complete` and `issues`. In `assert` and semantic `wait`, insufficient evidence is an operational error (exit 2), never a successful absence assertion. Empty, partial, or truncated Accessibility text is insufficient. A complete Accessibility result can establish absence only from the exposed tree, not from every pixel. OCR can support a positive text match above the confidence threshold; a weak match is insufficient. OCR cannot prove that text is absent from the image, so an OCR absence query is insufficient unless recognized matching text clearly makes the assertion fail. OCR cannot check whether a control is enabled. A valid semantic mismatch returns `fail` (exit 1); a semantic wait that does not reach its condition by the deadline returns a timeout failure.

Semantic queries use a small Swift helper built against public macOS APIs. A first query compiles it into `.seer/cache` if needed and requires `swiftc` from Xcode Command Line Tools; allow a longer first-query deadline with `--timeout 30` if compilation is slow. Capture and image verification do not require the helper. Accessibility messaging has an explicit API timeout facility ([Apple Accessibility API](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout)); it cannot make an app expose a complete tree. Window-to-Accessibility mapping must be unique; stale or ambiguous matches are errors. OCR uses Apple's Vision text-recognition request, which reports recognized text observations and their image regions ([Apple Vision documentation](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)). This is text evidence, not a semantic control tree.

Inspection reports and any OCR captures stay local under `.seer/inspect`. Assertion and wait verdicts are emitted on stdout and refer to these reports; save stdout if you need to retain the verdict. The visual comparison replay command does not replay semantic verdicts. No API key or external network request is used. Inspect the emitted source and confidence evidence before relying on an assertion.

## Result handling and limits

Commands emit one JSON result on stdout and diagnostics on stderr. Exit 0 means the requested condition passed; exit 1 means a valid comparison/assertion failed or a condition wait reached its deadline; exit 2 means input, permission, dependency, query timeout, or evidence was insufficient; exit 3 means a visual baseline is missing. A wait whose reads fail or whose evidence is insufficient returns exit 2. Read the JSON evidence and error code before deciding whether to retry.

Keep captures, diffs, reports, and run bundles under `.seer/`. Seer does not interact with the app or infer approval to create or replace a visual baseline. See [Visual loop](visual-loop.md) for image comparison and evidence bundle details, and [v0.7 validation](v0.7-validation.md) for the pending validation record. [v0.6 validation](v0.6-validation.md) is a historical record.

<a id="later-directions"></a>

## Roadmap

- v0.8: a thin local MCP adapter and evaluated agent workflows.
- v0.9: installation, compatibility, and release hardening based on actual use.
