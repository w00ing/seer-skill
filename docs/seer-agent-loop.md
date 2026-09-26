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
  --min-confidence 0.8 --timeout 60 --json

# Wait until a semantic condition is observed, or wait for pixel stability.
"$SEER" wait --text "Ready" --window-id 12345 --timeout 10 --interval 0.25 --json
"$SEER" wait --stable --window-id 12345 --timeout 10 --interval 0.25 \
  --stable-for 1 --max-diff-percent 0 --json
```

`assert` supports `--text`, `--text-absent`, `--enabled`, and `--disabled`; add `--role AXButton` to restrict a control-state query to that exact role. Text matching is case-sensitive and `--match` is `contains` by default or `exact`. Control names match exactly. Semantic `wait` accepts those conditions or the existing `--stable` pixel condition.

Without `--source`, semantic commands query Accessibility. OCR is selected only with `--source ocr`; Seer never switches sources automatically. `inspect` and `assert` allow 30 seconds by default; semantic `wait` allows 10. The budget includes helper build and OCR work; use an explicit longer value such as `--timeout 60` for OCR or a cold helper build. A region uses `X,Y,WIDTH,HEIGHT` in window-local points with a top-left origin. Accessibility filtering uses the centers of element bounds; OCR crops the corresponding image region and maps recognized bounds back to the window. `--ignore-rect` remains a visual-comparison option measured in baseline PNG pixels.

`inspect` returns exit 0 when a query completes, even if its `complete` field is false; inspect callers must check `complete` and `issues`. In `assert` and semantic `wait`, insufficient evidence is an operational error (exit 2), never a successful absence assertion. Empty, partial, or truncated Accessibility text is insufficient. A complete Accessibility result can establish absence only from the exposed tree, not from every pixel. OCR can support a positive text match above the confidence threshold; a weak match is insufficient. OCR cannot prove that text is absent from the image, so an OCR absence query is insufficient unless recognized matching text clearly makes the assertion fail. OCR cannot check whether a control is enabled. A valid semantic mismatch returns `fail` (exit 1); a semantic wait that does not reach its condition by the deadline returns a timeout failure.

Semantic queries use a small Swift helper built against public macOS APIs. A first query compiles it into `.seer/cache` if needed and requires `swiftc` from Xcode Command Line Tools. Capture and image verification do not require the helper. Accessibility messaging has an explicit API timeout facility ([Apple Accessibility API](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout)); it cannot make an app expose a complete tree. Window-to-Accessibility mapping must be unique; stale or ambiguous matches are errors. OCR uses Apple's Vision text-recognition request, which reports recognized text observations and their image regions ([Apple Vision documentation](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)). A native fixture check misread its title despite confidence 1.0, so confidence is not a guarantee of exact text. OCR is text evidence, not a semantic control tree.

Inspection reports and any OCR captures stay local under `.seer/inspect`. Assertion and wait verdicts are emitted on stdout and refer to these reports; save stdout if you need to retain the verdict. The visual comparison replay command does not replay semantic verdicts. No API key or external network request is used. Native fixture validation covered OCR text, regions, waits, and insufficient-absence evidence at 2× display scale; broader app and display-scale coverage remains unmeasured. Inspect the emitted source and confidence evidence before relying on an assertion.

## Result handling and limits

Commands emit one JSON result on stdout and diagnostics on stderr. Exit 0 means the requested condition passed; exit 1 means a valid comparison/assertion failed or a condition wait reached its deadline; exit 2 means input, permission, dependency, query timeout, or evidence was insufficient; exit 3 means a visual baseline is missing. A wait whose reads fail or whose evidence is insufficient returns exit 2. Read the JSON evidence and error code before deciding whether to retry.

Keep captures, diffs, reports, and run bundles under `.seer/`. Seer does not interact with the app or infer approval to create or replace a visual baseline. See [Visual loop](visual-loop.md) for image comparison and evidence bundle details, and [v0.7 validation](v0.7-validation.md) for the measured validation record. [v0.6 validation](v0.6-validation.md) is a historical record.

<a id="later-directions"></a>

## Roadmap

- v0.8: a thin local MCP adapter and evaluated agent workflows.
- v0.9: isolated installation/update evaluation, measured compatibility, JSON contracts, report summaries, explicit evidence export, and minimal diagnostics.

## Local MCP entry point

The optional v0.8 MCP server exposes `seer_run` and `seer_help` over stdio. It invokes the installed CLI from an explicit project root, without a shell. For example, `seer_run({"arguments":["assert","--window-id","12345","--text","Saved","--json"]})` uses exactly the CLI assertion implementation. Read its `status`, source, confidence, issues, and artifact paths as above. `fail` and `needs_baseline` have MCP `isError: false`; execution errors have `isError: true`. Never infer success from the MCP wrapper alone.

Errors and missing baselines include `recovery: {retryable, next_action}` in both CLI and MCP results. Retryability describes whether a later attempt may help after the suggested action; it grants no permission to modify app state, privacy settings, or a baseline. Baseline creation and replacement are blocked by the adapter and require the approved CLI workflow. The adapter returns local paths, not screenshot bytes; inspect the referenced fresh image with the host's image tool.

Use an explicit CLI timeout below the server's `--command-timeout` (default 60 seconds), and a host tool timeout above the server budget. Cancellation terminates the owned CLI process and unwinds native-worker cleanup. See [MCP setup and host evaluation](mcp-integration.md) and [v0.8 validation](v0.8-validation.md).

## Preserve and hand off results

Use `seer report RESULT_JSON_OR_BUNDLE --out summary.md --json` (or the same arguments through `seer_run`) to turn saved results into Markdown. Report generation has its own status: retain the source verdict when deciding whether the UI passed. Add `--export run.zip` only when evidence export is requested and use a comparison bundle as input. The resulting archive remains local and can contain screenshot text and local metadata.

Use `seer diagnostics --json` for allowlisted environment information without window content or paths. See [evidence retention and sharing](evidence-management.md), [JSON compatibility](json-contract.md), and [v0.9 validation](v0.9-validation.md). Never infer tested host compatibility from protocol compliance alone. Claude Code 2.1.283 passed the representative MCP comparison task; Claude-driven native window workflows remain untested.
