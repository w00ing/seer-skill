# Seer

**Visual verification for coding agents on macOS.**

Seer gives Codex and Claude Code a repeatable native-UI feedback loop: capture a running app window, inspect the visible result, and compare it with an explicitly approved baseline. Screenshots, diffs, recordings, and machine-readable reports stay local under `.seer/`.

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

Image verification requires Pillow in the active `python3` environment. If it is not already available:

```bash
python3 -m venv .local/venv
source .local/venv/bin/activate
python -m pip install pillow
```

Then run the underlying scripts directly from a clone:

```bash
current=$(bash skills/seer/scripts/capture_app_window.sh)

# No baseline is silently approved. This returns needs_baseline (exit 3).
bash skills/seer/scripts/loop_compare.sh "$current" settings

# Create it only after reviewing the capture.
bash skills/seer/scripts/loop_compare.sh --create-baseline "$current" settings

# After a UI change, allow at most 0.5% changed pixels.
current=$(bash skills/seer/scripts/capture_app_window.sh)
bash skills/seer/scripts/loop_compare.sh --max-diff-percent 0.5 "$current" settings
```

Verification prints one JSON object to stdout and writes the current image, diff, history, and report under `.seer/loop/`.

| Status | Exit | Meaning |
|---|---:|---|
| `pass` | 0 | Changed pixels are within the allowed threshold. |
| `fail` | 1 | The visual difference exceeds the threshold. |
| `needs_baseline` | 3 | No approved baseline exists; Seer did not create one. |

The default threshold is 0%. Other tool or input errors return non-zero and write diagnostics to stderr.

## Demo

![Seer capturing and verifying a macOS app window](assets/seer-demo.gif)

[View the full demo video](assets/seer-demo.mov)

## Core commands

| Task | Command |
|---|---|
| Capture a visible app window | `bash skills/seer/scripts/capture_app_window.sh` |
| Verify against a named baseline | `bash skills/seer/scripts/loop_compare.sh <current.png> <name>` |
| Record a short app flow | `bash skills/seer/scripts/record_app_window.sh --duration 3` |
| Summarize a recording | `bash skills/seer/scripts/summarize_video.sh <video.mov> --sheet --gif` |

Use `--help` on any command for its complete options. Pillow is required for image comparison and annotation; ffmpeg and ffprobe are optional unless you use video workflows.

## Advanced workflows

- `record_screen.sh`: full-display or region recording, including manual-stop ffmpeg capture.
- `extract_frames.sh`: fixed-FPS frame extraction.
- `mockup_ui.sh` and `annotate_image.py`: screenshot annotations; run `annotate_image.py --spec-help` for the JSON schema.
- `excalidraw_from_text.py`: natural-language-to-Excalidraw wireframes. See [Excalidraw wireframing](docs/excalidraw-wireframing.md).
- `type_into_app.sh`: explicit, state-changing typing through System Events.

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
- Wrong window: pass the exact process name to `capture_app_window.sh`.
- Typing fails: grant Accessibility and Automation → System Events permissions.
- Diff command reports missing Pillow: install it in the `python3` environment used by Seer.

## Development

```bash
python3 -m venv .local/venv
.local/venv/bin/python -m pip install pillow
.local/venv/bin/python -m unittest discover -s tests -v
```

Shell scripts are checked on macOS CI. Claude packaging can be validated locally with:

```bash
claude plugin validate --strict .
```

## License

MIT
