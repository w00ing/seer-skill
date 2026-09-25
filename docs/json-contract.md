# Seer JSON contract

This document describes the machine-readable boundary used by the `seer` CLI and its MCP adapter. The accompanying schemas are Draft 2020-12 JSON Schemas:

- [CLI and MCP result envelope](../schemas/cli-envelope.schema.json) validates the shared envelope, not each command's complete payload.
- [Comparison bundle manifest](../schemas/comparison-bundle-manifest.schema.json) validates the manifest's main structure, not the files or their contents.

The CLI emits one JSON object on stdout for commands with a result. Human-readable diagnostics may also be written to stderr. `--json` is accepted by the result commands; JSON is their normal output. Consumers should parse JSON rather than depend on whitespace or key order.

## Envelope version 1

Every CLI result has `schema_version: 1`, `operation`, and `status`. `operation` names the result operation; a successful `verify --create-baseline` result uses `baseline_create`. It is `null` for a usage error that occurred before a command could be identified. The common envelope schema permits the CLI operations `doctor`, `windows`, `capture`, `verify`, `baseline_create`, `inspect`, `assert`, `wait`, `report`, and `diagnostics`, plus the MCP adapter operation names used for adapter errors. Unknown properties are permitted so additions do not invalidate existing consumers.

The `status` value and process exit code have this mapping:

| Status | CLI exit code | Meaning |
| --- | ---: | --- |
| `pass` | 0 | The operation completed successfully. For a `verify --create-baseline` run, the result operation is `baseline_create`. |
| `fail` | 1 | A completed verification/assertion/wait found an ordinary mismatch or timed out. This is a domain result, not an operational error. |
| `error` | 2 | The operation could not produce a trustworthy result or complete its requested work. `error` contains a machine-readable `code` and human-readable `message`. |
| `needs_baseline` | 3 | Verification found no approved baseline. It is a domain result, not an operational error. The response includes a `next_action` and, when guidance is enabled, recovery guidance. |

The envelope schema requires `error` for `status: error` and prohibits it for the other statuses. It also constrains the statuses produced by each known operation. Additional command result fields such as `windows`, `capture`, `elements`, `evidence`, and `metrics` are intentionally not validated there. A valid envelope does not prove that an operation's complete evidence payload is valid.

In MCP, `seer_run` exposes the CLI result as `structuredContent` and as JSON text. Its `isError` flag is true only for `status: error`; `fail` and `needs_baseline` remain ordinary tool results. MCP adapter failures use the same error envelope and may identify `seer_run` or `mcp` as the operation. A successful `seer_help` response is help text rather than a CLI result envelope.

The `report` command returns `status: pass` when it successfully produces a Markdown report, regardless of the source result. Its `source_operation` and `source_status` fields retain the source result, including errors from commands such as `capture` or `assert`; `source_operation` can be `null` when the source was a command-line usage error, and a source `needs_baseline` status is only valid with `source_operation: verify`. A report-generation problem uses the ordinary `status: error` envelope. `report --export` accepts a bundle directory as input; a report JSON file with `--export` is an `invalid_arguments` error. `artifacts.report` identifies the generated report, and `artifacts.export` is present with the archive path when export is requested.

`diagnostics` success means the allowlisted diagnostics were collected; absent dependencies are reported with `available: false` and do not make the command fail. The output reports the Seer version, platform value with macOS release and architecture (null when unavailable), Python version/implementation, availability and version for Pillow, MCP, and jsonschema (version is null when unavailable), plus only the availability of `ffmpeg` and `ffprobe`. It does not report environment variables, user or host names, filesystem paths, window/capture data, authentication state, or permission state.

## Versioning and compatibility

Each independently persisted JSON document carries its own `schema_version`. In particular, CLI results, comparison reports/manifests, and `seer.capture` metadata are separate versioned documents even when they currently use version `1`. A version number on one document does not validate another document.

For version 1, consumers should require the documented essential fields, tolerate unknown properties, and handle unknown error codes with a generic recovery path. Error `message` text is for people and may change; `error.code` is the programmatic identifier. New optional properties and codes can be added. A producer changing required fields or established meaning should advance the relevant document's `schema_version`; consumers should reject unsupported versions rather than guess.

Both schemas use `additionalProperties: true` for extension points and deliberately leave much of command-specific evidence open. The CLI envelope schema validates the currently documented diagnostics field types and the report summary fields, but it does not validate the full `verify` report, UI inspection tree, assertion evidence, or actual dependency inventory. It also does not enforce privacy policy on unknown extension fields. A valid envelope is not a complete evidence or privacy audit.

## Comparison bundle manifest version 1

`verify` writes a comparison report (`report.json`) and a `manifest.json` for a completed run. The manifest declares an immutable bundle containing baseline and current image snapshots, the report, and, when available, a diff image and capture metadata sidecars. `files` records a relative bundle filename, lowercase SHA-256 digest, and byte size for each declared file. `paths` repeats the role-to-filename map. A manifest is not produced for `needs_baseline`, because no comparison bundle is created in that case.

The comparison `report.json` has its own `schema_version: 1` result payload (with operation `verify` or `baseline_create`). This release does not publish a standalone schema for every comparison report field. The `report` command checks the common result requirements and the comparison verdict relationship with its manifest; the CLI envelope schema also checks only the shared envelope and its documented operation/status rules.

The manifest schema checks required keys, value types, status relationships, digest syntax, and simple relative filenames. JSON Schema alone cannot confirm that `paths` agrees with `files`, that an artifact exists, that it stays inside the bundle after filesystem resolution, or that its bytes match `sha256` and `size_bytes`. Consumers that use a manifest as input must perform those checks; the `report` command also rejects unsafe paths, duplicate/symlink declarations, missing or changed files, and inconsistent report/image declarations. The schema's structural validity is not evidence of bundle integrity.

The current manifest deliberately records some local provenance. `images.*.source_path` may contain an absolute local path. Verified capture metadata can include a capture timestamp, process name, and `window_id`; the copied `metadata` object retains recognized sidecar data and leaves unknown sidecar fields opaque for forward compatibility. Treat manifests and exported evidence as potentially sensitive when sharing them.

Native window IDs, process IDs, and accessibility element IDs describe observations from a particular host/session. They can change when windows or app processes are recreated and must not be treated as durable identifiers. The schemas keep extension objects open and do not assign stable meaning to unknown fields or unknown capture-sidecar data.

## Error codes and recovery

The table lists codes emitted by the current CLI/MCP paths and the v0.9 `report` command. Error messages and optional `details` add context. Error details can contain local paths or helper diagnostics; preserve their privacy when exporting logs.

| Code | Meaning and recovery |
| --- | --- |
| `invalid_arguments` | Correct the command flags or values using `seer <command> --help`. |
| `dependency_missing` | Install the named dependency in the Python environment used to run Seer, then rerun `doctor`. |
| `platform_unsupported` | Run a macOS-only window operation on a supported macOS host. |
| `not_ready` | Inspect `doctor` capabilities, resolve the listed prerequisite, and run `doctor` again. |
| `accessibility_required` | Have the user review Accessibility access for the calling app, then retry. |
| `filesystem_error` | Check the reported input/output path, available space, and write/read access. |
| `subprocess_failed`, `internal_error`, `native_build_failed` | Inspect the operation and diagnostic details; repair or update the failing helper/toolchain, then retry. |
| `invalid_subprocess_output`, `mcp_invalid_output` | The helper/CLI response was malformed or inconsistent. Inspect diagnostics and update or repair the Seer installation before retrying. |
| `stale_window`, `window_not_found`, `window_unavailable` | Run `windows` again and select the intended window's current ID. |
| `window_ambiguous` | Inspect the window list and choose a unique target; do not guess which accessibility window matches. |
| `window_changed` | Keep the target window's geometry stable while inspection runs, then retry. |
| `accessibility_timeout`, `query_timeout`, `mcp_command_timeout` | Check whether the target app is responsive. Retry with a suitable bounded timeout; for MCP, increase the server and host timeouts together. |
| `accessibility_failed` | Review the app's accessibility response and permission state; inspect `details` before retrying. |
| `ocr_failed` | Check that the target window can be captured and that the local Vision OCR worker can run, then retry. |
| `capture_geometry_mismatch` | Retry after the target window's dimensions and scale stop changing. |
| `region_scope_unknown` | Use complete evidence or a supported source/region; do not infer absence from evidence whose region cannot be established. |
| `source_unsupported` | Use Accessibility (`--source ax`) for control-state assertions. |
| `insufficient_evidence` | Inspect the source, issues, and region; use an assertion supported by complete evidence. Do not treat it as success. |
| `selector_ambiguous` | Narrow the exact control name/role so only one control matches. |
| `image_not_found`, `invalid_image` | Supply an existing, readable image in a supported format. |
| `image_size_mismatch`, `image_scale_mismatch` | Capture at the approved baseline's dimensions and scale; use `--resize` only when explicitly requested. |
| `baseline_exists` | Use the already approved baseline or obtain approval before replacing it. |
| `baseline_approval_required` | Ask the user to approve baseline creation/replacement, then use the explicit CLI baseline flag. MCP cannot create or replace approved baselines. |
| `mcp_invalid_arguments` | Pass a supported MCP command and a non-empty array of CLI argument strings; use `seer_help` for command syntax. |
| `mcp_cli_unavailable` | Repair the CLI installation or execution environment, then retry the MCP request. |
| `mcp_unknown_tool` | Use one of the tools advertised by the MCP server. |
| `invalid_evidence` | Supply a readable JSON report or comparison bundle with the required evidence fields. |
| `unsupported_schema` | Export or regenerate the source using a schema version supported by this Seer release. |
| `evidence_tampered` | Restore the declared bundle files or generate a fresh bundle from trusted inputs. |
| `evidence_too_large` | Summarize without `--export` or choose a complete bundle within the 128 MiB export limit. |
| `unsafe_path` | Use bundle-contained relative filenames without traversal, symlinks, or duplicate declarations. |
| `output_exists` | Choose an unused report/export destination. |

Some lower-level helper failures, including capture permission failures, are normalized by the CLI as `subprocess_failed`; the message may still describe the specific permission or missing prerequisite. Do not key automation on message text.

UI inspection also returns non-error observation issues in its `issues` array. Examples include `children_unavailable`, `children_invalid`, `hidden_state_unavailable`, `hidden_state_invalid`, `role_unavailable`, `name_unavailable`, `subrole_unavailable`, `secure_field_value_not_read`, `value_unavailable`, `enabled_unavailable`, `element_bounds_unavailable`, `depth_limit_reached`, `node_limit_reached`, `accessibility_cycle_detected`, and `region_scope_unknown`. These describe partial observations rather than an operational `error.code`; inspect `complete` and `issues` before drawing conclusions. Assertions that require missing evidence return `insufficient_evidence`.
