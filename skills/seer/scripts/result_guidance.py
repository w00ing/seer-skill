"""Add recovery guidance without changing Seer's verdict or evidence."""

_GUIDANCE = {
    "invalid_arguments": (False, "Read seer --help for this command and correct its arguments."),
    "platform_unsupported": (False, "Run window operations on a supported macOS host."),
    "dependency_missing": (False, "Install the named dependency in the Python environment used to launch Seer, then run doctor."),
    "accessibility_required": (False, "Ask the user to review Accessibility access for the calling app; do not change permissions automatically."),
    "screen_recording_required": (False, "Ask the user to review Screen Recording access for the calling app; do not change permissions automatically."),
    "not_ready": (True, "Inspect the doctor capability report, resolve the reported prerequisite, then run doctor again."),
    "stale_window": (True, "Run windows again and select the intended visible window's new ID before retrying."),
    "window_not_found": (True, "Run windows again and select the intended visible window's current ID before retrying."),
    "window_unavailable": (True, "Check that the intended window is visible, run windows again, and select its current ID before retrying."),
    "window_ambiguous": (False, "Inspect the window list and choose a uniquely identifiable target; do not guess an Accessibility window match."),
    "accessibility_timeout": (True, "Check that the intended app is responsive, then retry the bounded query."),
    "query_timeout": (True, "Check that the intended window is responsive; retry with a larger bounded --timeout if needed."),
    "mcp_command_timeout": (True, "Check the window and CLI budget; increase the server --command-timeout and host tool timeout together if needed."),
    "filesystem_error": (False, "Check the reported path, available space, and write access before retrying."),
    "image_size_mismatch": (False, "Capture at the baseline dimensions; use --resize only when explicitly requested."),
    "image_scale_mismatch": (False, "Capture at the baseline scale; use --resize only when explicitly requested."),
    "baseline_approval_required": (False, "Ask the user to approve baseline creation or replacement, then use the CLI with the approved baseline flag. MCP cannot write baselines."),
    "insufficient_evidence": (False, "Inspect the source, issues, and region. Use a supported assertion with sufficient evidence; do not treat this result as success."),
    "invalid_evidence": (False, "Use a Seer result JSON file or an intact comparison bundle with a supported schema."),
    "unsupported_schema": (False, "Use a Seer version that supports the result or comparison bundle schema."),
    "evidence_tampered": (False, "Use the original untouched comparison bundle; Seer will not export files that fail their recorded checksums."),
    "evidence_too_large": (False, "Summarize without --export or select a bundle within the 128 MiB export limit; preserve the original evidence."),
    "unsafe_path": (False, "Inspect the bundle manifest and keep declared files inside the bundle using safe relative paths."),
    "output_exists": (False, "Choose a new output path; Seer preserves existing files."),
}


def with_guidance(payload):
    """Return a shallow copy enriched only for errors or missing baselines.

    retryable means another attempt may help after the stated action; it is not
    permission to change app state, permissions, or an approved baseline.
    """
    if payload.get("status") == "needs_baseline":
        recovery = {
            "retryable": False,
            "next_action": "Inspect the current image and ask the user to approve a baseline. Only after approval, use the CLI --create-baseline option.",
        }
    elif payload.get("status") == "error":
        code = payload.get("error", {}).get("code")
        retryable, action = _GUIDANCE.get(code, (
            False, "Inspect the error and any saved evidence; resolve the cause before retrying. Do not treat this result as success.",
        ))
        recovery = {"retryable": retryable, "next_action": action}
    else:
        return payload
    return {**payload, "recovery": recovery}


def error_payload(operation, code, message, **extra):
    return with_guidance({
        **extra,
        "schema_version": 1,
        "operation": operation,
        "status": "error",
        "error": {"code": code, "message": message},
    })
