# Compatibility and supported scope

Seer targets local macOS use. Support claims are limited to the environments and checks below; an installation minimum is not a claim that every intervening release was tested.

| Environment | Coverage |
|---|---|
| macOS 27.0, Apple Silicon, 2× fixture window | Actual exact-window capture, Accessibility assertions/wait, stale-window failure, and MCP/native fixture checks. Prior Vision OCR checks are recorded in v0.7. |
| Python 3.9.6 with Pillow 11.3.0 | Locally exercised standalone CLI, synthetic comparisons, maintenance commands, and isolated skill installation/update. Optional MCP tests are skipped when the SDK is absent. |
| Python 3.13.12 with Pillow 12.3.0 and MCP SDK 2.2.0 | Locally exercised CLI, MCP protocol and native fixture, maintenance commands, and isolated skill installation/update. |
| `macos-26` CI, Python 3.9 and 3.13 | The workflow runs the automated suites and Swift helper compilation on both rows; the 3.13 row also exercises the MCP SDK. CI does not establish interactive desktop permissions or native capture behavior. See the matching commit's Actions run for the actual runner/Python patch versions and outcome. |

The standalone CLI's maintained minimum is Python 3.9. Python 3.9 uses Pillow 11.x; the optional MCP installation requires Python 3.10+ and its own [dependency file](../skills/seer/scripts/requirements-mcp.txt). The measured MCP interpreter is 3.13.12. Python 3.10–3.12 and newer interpreter versions are not locally validated by this record. A cold semantic query needs `swiftc` from Xcode Command Line Tools. Video workflows additionally need ffmpeg/ffprobe.

Native window operations require a visible, unlocked macOS desktop plus the relevant Screen Recording and Accessibility permissions. Seer does not modify those permissions. Image-only synthetic tests do not establish window access, and `diagnostics` intentionally does not probe it.

Only the 2× native fixture geometry has been measured. Real 1×, mixed-scale, display-switching, and unusual DPI configurations remain untested. Image size/DPI mismatch regressions use synthetic images and do not establish those physical-display scenarios. Comparisons reject size and known-scale mismatches unless `--resize` explicitly opts into normalization; unknown scale remains unknown.

macOS versions outside the measured 27.0 native environment and configured 26 CI environment, Intel hardware, and non-macOS native operation have no coverage claim here. Host installations and updates are evaluated by copying into isolated temporary skill directories; no existing user installation is overwritten. Codex's representative model-backed task passed in v0.8. Claude Code 2.1.283 passed the [same representative task against Seer v0.9](v0.9-validation.md#claude-code-host-follow-up): help plus matching, changed, and missing-baseline comparisons, with baseline preservation. This single synthetic task does not establish Claude-driven native capture, OCR, maintenance commands, or marketplace installation.

## Maintaining the matrix

The checked-in workflow uses explicit OS/Python rows. For another environment, run the automated suite, isolated [distribution evaluation](distribution-check.md), and relevant [native fixture checks](v0.9-validation.md). Record OS/architecture, interpreter and dependency versions, pixel/point dimensions, correct and incorrect verdict counts, and timing boundaries. Add a support claim only after collecting that evidence. Keep untested permission, scale, and host combinations visible rather than extrapolating from a passing unit test.

Compatibility for machine-readable consumers is specified separately in the [JSON contract](json-contract.md). The package version is not the JSON schema version.
