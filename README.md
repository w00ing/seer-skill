# Seer

**Visual verification for coding agents on macOS.**

Seer gives Codex and Claude Code one machine-readable CLI for a repeatable native-UI feedback loop: check capabilities, find a window, capture it, inspect the visible result, and compare it with an explicitly approved baseline. Screenshots, diffs, recordings, and reports stay local under `.seer/`.

Seer is an evidence layer, not another desktop automation framework. Your agent changes the code; Seer verifies what actually appeared on screen.

[![release](https://img.shields.io/github/v/release/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/releases)
[![license](https://img.shields.io/github/license/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/blob/main/LICENSE)

> macOS only. No model API key or background daemon. Window capture requires Screen Recording and Accessibility permissions.

## Install

### Codex

Run `$skill-installer`, then ask:

```text
Install the `seer` skill from GitHub repository `w00ing/seer-skill` at path `skills/seer`.
```

### Claude Code

```text
/plugin marketplace add https://github.com/w00ing/seer-skill.git
/plugin install seer-skill@seer
```

If the marketplace already exists, run `/plugin marketplace update seer` first.

## Try it

Codex:

```text
$seer Capture the frontmost app, inspect the visible UI, and verify my latest change.
```

Claude Code plugin:

```text
/seer-skill:seer Capture the frontmost app, inspect the visible UI, and verify my latest change.
```

Or ask either agent:

```text
Use Seer to capture the Settings window and compare it with the approved `settings` baseline. If no baseline exists, report it and ask before creating one.
```

## 30-second verification loop

Capture validation, image verification, and annotation require Pillow in the active `python3` environment. If it is not already available:

```bash
python3 -m venv .local/venv
source .local/venv/bin/activate
python -m pip install pillow
```

Then run the shipped CLI from a clone:

```bash
SEER=skills/seer/scripts/seer

"$SEER" doctor --json
"$SEER" windows --json
# Select a returned window_id; it identifies one exact window, even when the app has several.
"$SEER" capture --window-id 12345 --out .seer/capture/current.png --json

# No baseline is silently approved. This returns needs_baseline (exit 3).
"$SEER" verify .seer/capture/current.png settings --json

# Inspect current.png, then create a baseline only after approval.
"$SEER" verify .seer/capture/current.png settings --create-baseline --json

# After a UI change, allow at most 0.5% changed pixels.
"$SEER" capture --window-id 12345 --out .seer/capture/current.png --json
"$SEER" verify .seer/capture/current.png settings --max-diff-percent 0.5 --json
```

Each CLI invocation emits one JSON result to stdout, including on operational errors; diagnostics go to stderr. `--help` prints ordinary help text. Capture paths are returned as `artifacts.current`. Verification writes the current image, diff, history, and report under `.seer/loop/`.

| Status | Exit | Meaning |
|---|---:|---|
| `pass` | 0 | Changed pixels are within the allowed threshold. |
| `fail` | 1 | The visual difference exceeds the threshold. |
| `error` | 2 | A command, permission, dependency, or input failed. |
| `needs_baseline` | 3 | No approved baseline exists; Seer did not create one. |

The default threshold is 0%. Every operational error emits one JSON object on stdout and a human-readable diagnostic on stderr. Its common shape is `{"schema_version":1,"operation":"capture","status":"error","error":{"code":"subprocess_failed","message":"..."}}`; `operation` is the recognized subcommand or `null` when none could be identified. `doctor` errors also include the capability report and `frontmost_process`. The `--help` forms are the exception and print ordinary help text. Error codes are `invalid_arguments`, `platform_unsupported`, `dependency_missing`, `subprocess_failed`, `invalid_subprocess_output`, `accessibility_required`, `filesystem_error`, and `not_ready`.

## Demo

![Seer capturing and verifying a macOS app window](assets/seer-demo.gif)

[View the full demo video](assets/seer-demo.mov)

## Core commands

| Task | Command |
|---|---|
| Check required capabilities | `skills/seer/scripts/seer doctor --json` |
| List visible app windows | `skills/seer/scripts/seer windows --json` |
| Capture an exact visible window | `skills/seer/scripts/seer capture --window-id <id> --json` |
| Verify against a named baseline | `skills/seer/scripts/seer verify <current.png> <name> --json` |
| Record a short app flow | `bash skills/seer/scripts/record_app_window.sh --duration 3` |
| Summarize a recording | `bash skills/seer/scripts/summarize_video.sh <video.mov> --sheet --gif` |

Use `--help` on the CLI or a subcommand for complete options. `windows` returns a session-scoped native `window_id`; it survives window movement and reordering, but becomes stale when the window closes or is recreated. The 1-based `index` remains informational. `capture --process` remains a compatibility fallback that captures the process's first window. Capture validates a fresh temporary PNG with the active `python3` and Pillow before atomically replacing the requested output; a failed capture or validation leaves an existing output file unchanged. Pillow is required for capture validation, image comparison, and annotation; ffmpeg and ffprobe are optional unless you use video workflows.

`doctor` reports the frontmost process separately from the visible-window Accessibility probe: `frontmost_process` is its own field, while the probe result is reported by `capabilities.window_query.authorized`. A false result means Seer could not confirm accessible visible-window access; it does not by itself distinguish a permission denial from the absence of a visible window. Screen Recording permission is checked when capture runs.

Exact-ID capture checks that the window is on-screen before and after capture. This prevents macOS from returning cached pixels for a closed window as a successful new capture. Minimized or hidden windows must be made visible and rediscovered first.

## Advanced workflows

- `record_screen.sh`: full-display or region recording, including manual-stop ffmpeg capture.
- `capture_app_window.sh` and `loop_compare.sh`: lower-level capture and visual-loop commands used by the unified CLI.
- `extract_frames.sh`: fixed-FPS frame extraction.
- `mockup_ui.sh` and `annotate_image.py`: screenshot annotations; run `annotate_image.py --spec-help` for the JSON schema.
- `excalidraw_from_text.py`: natural-language-to-Excalidraw wireframes. See [Excalidraw wireframing](docs/excalidraw-wireframing.md).
- `type_into_app.sh`: explicit, state-changing typing through System Events.

### Excalidraw examples

| Generated sign-in wireframe | Explicit library components |
| --- | --- |
| ![Generated sign-in wireframe](assets/excalidraw-auth-sign-in.png) | ![Generated library components](assets/excalidraw-library-components.png) |

See [Visual loop internals](docs/visual-loop.md) for the underlying image metrics.

## Artifact layout

```text
.seer/
├── capture/      window screenshots
├── record/       recordings, frames, contact sheets, and GIFs
├── mockup/       annotated screenshots and specs
├── excalidraw/   generated .excalidraw scenes
└── loop/
    ├── baselines/
    ├── latest/
    ├── history/
    ├── diffs/
    └── reports/
```

Set `SEER_OUT_DIR` to change the output root or `SEER_LOOP_DIR` to change only visual-verification storage. Add `.seer/` to the target project's `.gitignore` unless you intentionally version its baselines.

## Permissions and troubleshooting

- `error: window not found`: start the app, check its process name, and ensure it has a visible window.
- Empty or black capture: grant Screen Recording permission to the terminal running the agent.
- Wrong or stale window: rerun `windows --json`, then pass the returned `window_id` to `capture`.
- Typing fails: grant Accessibility and Automation → System Events permissions.
- Capture or diff reports missing Pillow: install it in the active `python3` environment used by Seer.

## v0.5 validation

The reproducible macOS validation checklist, recorded results, and remaining permission-test gaps are in [v0.5 validation](docs/v0.5-validation.md). CI uses command stubs for deterministic behavior; native window behavior is exercised separately with an opt-in AppKit fixture.

## Development

```bash
python3 -m venv .local/venv
.local/venv/bin/python -m pip install pillow
.local/venv/bin/python -m unittest discover -s tests -v
python3 skills/seer/scripts/test_excalidraw.py
```

Shell scripts are checked on macOS CI. Claude packaging can be validated locally with:

```bash
claude plugin validate --strict .
```

## License

MIT
