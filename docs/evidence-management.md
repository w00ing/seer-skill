# Summaries, exports, and local retention

Seer stores evidence locally. It does not upload reports, run a retention daemon, or delete old evidence automatically.

## Summarize a result

Save a CLI result as JSON, or use the immutable comparison bundle returned by `verify` as `artifacts.bundle`:

```bash
SEER=skills/seer/scripts/seer
"$SEER" report .seer/loop/runs/<run-id> --out .seer/reports/<run-id>.md --json
# An assertion result saved from stdout can also be summarized.
"$SEER" report .seer/assert-result.json --out .seer/reports/assertion.md --json
```

The report command's success means it produced the summary. Read the original verdict inside the summary and returned result before deciding whether the UI passed. A successfully summarized `fail` or `error` remains a failed or inconclusive verification. Summary generation does not recapture an app, replay a comparison, or approve a baseline.

## Export a comparison bundle

```bash
"$SEER" report .seer/loop/runs/<run-id> \
  --out .seer/reports/<run-id>.md --export .seer/exports/<run-id>.zip --json
```

Export is explicit and accepts a comparison bundle directory, not arbitrary paths referenced by a result JSON file. The exporter checks manifest-declared files and their hashes, rejects unsafe paths and symlinks, and refuses to replace existing outputs. Choose new output names for another run. Source bundles and active baselines remain unchanged.

ZIP export accepts at most 128 MiB of declared evidence including the manifest, bounding its memory use. A larger bundle returns `evidence_too_large` before producing outputs; summarize it without `--export` or choose a smaller complete bundle. JSON inputs and bundle reports are limited to 4 MiB.

The ZIP contains local evidence, including screenshot content and original report/manifest metadata. Those files can contain window text, app names, and local paths. Export is not anonymization. Inspect the archive before choosing to share it; Seer does not send it anywhere. A Markdown summary can also contain text from the source result.

Checksums establish consistency with the supplied manifest; they do not authenticate its author or prove that the captured UI was correct. Preserve the original bundle for pixel replay as described in [Visual loop](visual-loop.md).

## Collect minimal diagnostics

```bash
mkdir -p .seer
"$SEER" diagnostics --json > .seer/diagnostics.json
```

This command reports an allowlist of version, operating-system, architecture, and dependency information. It does not query windows or privacy permissions, read screenshots, enumerate environment variables, or include usernames, hostnames, executable paths, credentials, or project paths. It therefore cannot establish that window capture permissions are ready. Use `doctor` locally when a permission/capture diagnosis is needed, and inspect that richer output before sharing it.

For a bug report, start with this diagnostic JSON, the command's option names, its error code, and a synthetic reproduction when possible. Remove personal text and paths from any command/result excerpt. Attach screenshots or ZIP evidence only when the recipient needs them and you have reviewed their contents. There is no automatic diagnostic submission.

## Retain and clean up deliberately

- Keep approved baselines separate from disposable run evidence. Removing `loop/baselines/` loses the reference images and makes future verification return `needs_baseline`.
- Keep the complete `loop/runs/<run-id>/` directory for any run that must remain replayable. Keeping only a diff or Markdown summary is insufficient.
- Review the age and disk usage of `capture/`, `record/`, `mockup/`, `loop/history/`, `loop/diffs/`, and `loop/runs/` periodically. Choose a retention period appropriate to the project; Seer imposes none.
- Before removing selected old runs, check that needed reports and exports are preserved and that no review relies on their local paths. Delete only the selected directories through your usual file-management tools. Do not remove the entire `.seer/` tree as a blanket cleanup.
- Include the configured `SEER_OUT_DIR` and `SEER_LOOP_DIR` in this review. Ignoring `.seer/` in Git prevents accidental commits; it does not encrypt files or provide a backup.

The [JSON contract](json-contract.md) describes result compatibility. The [v0.9 validation record](v0.9-validation.md) distinguishes measured behavior from untested environments.
