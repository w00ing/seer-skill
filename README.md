# seer-skill

Visual feedback capture skill for macOS app windows.

[![release](https://img.shields.io/github/v/release/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/releases)
[![license](https://img.shields.io/github/license/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/blob/main/LICENSE)

## Support

- macOS only

## Demo

![seer demo](assets/seer-demo.gif)

Full video: `assets/seer-demo.mov`

## Features

- Precise capture of a visible macOS app window
- Window video capture + frame extraction
- UI mockups by annotating screenshots (arrow, rectangle, text)
- Excalidraw scene generation (`.excalidraw`) from natural language prompts
- Scripted visual loop support (diffs, baselines, reports)
- Organized output layout under `.seer/` with latest artifacts

## Install

Codex (skill-installer UI):
- Run `$skill-installer`
- Ask: install GitHub repo `w00ing/seer-skill` path `seer`

Claude Code (plugin):
- `/plugin marketplace add w00ing/seer-skill`
- `/plugin install seer-skill@seer`
  - If the marketplace was previously added, run `/plugin marketplace update seer` before installing to pick up updates.
  - If you see an SSH clone error, add the marketplace via HTTPS instead:
    - `/plugin marketplace add https://github.com/w00ing/seer-skill.git`

Manual (Codex):
```bash
mkdir -p ~/.codex/skills
git clone https://github.com/w00ing/seer-skill.git /tmp/seer-skill
rsync -a /tmp/seer-skill/skills/seer/ ~/.codex/skills/seer/
```

Manual (Claude Code):
```bash
mkdir -p ~/.claude/skills
git clone https://github.com/w00ing/seer-skill.git /tmp/seer-skill
rsync -a /tmp/seer-skill/skills/seer/ ~/.claude/skills/seer/
```

## Use

- Skill name: `seer`
- Script: `skills/seer/scripts/capture_app_window.sh`
- Script: `skills/seer/scripts/record_app_window.sh`
- Script: `skills/seer/scripts/extract_frames.sh`
- Script: `skills/seer/scripts/summarize_video.sh`
- Script: `skills/seer/scripts/type_into_app.sh`
- Script: `skills/seer/scripts/mockup_ui.sh`
- Script: `skills/seer/scripts/excalidraw_from_text.py`
- Script: `skills/seer/scripts/annotate_image.py`
- Default output: `.seer/capture/app-window-<app>-YYYYMMDD-HHMMSS-<pid>-<rand>.png`
- Set `SEER_OUT_DIR` to change default output root (falls back to `SEER_TMP_DIR` for legacy behavior)
- Installed paths (Codex/Claude Code): `~/.codex/skills/seer/scripts` or `~/.claude/skills/seer/scripts`

### Window capture

Capture the frontmost app window (or a named process) as a precise PNG. Output is organized under `.seer/capture/` with app‑slugged filenames for easy tracking.

### Window recording + frames

Record a window region to `.mov` and extract frames for granular analysis.

### Video summary (representative frames)

Extract representative frames from a video using scene detection, keyframes, or fixed FPS. Optionally render a contact sheet or preview GIF.

Summary flags (when using `record_app_window.sh --summary`):
- `--summary-mode <scene|fps|keyframes>`: selection strategy (default: `scene`)
- `--summary-scene <threshold>`: scene-change sensitivity (default: `0.30`)
- `--summary-fps <n>`: sampling rate for `fps` mode (default: `2`)
- `--summary-max <n>`: cap frame count (default: `24`, `0` disables cap)
- `--summary-out <dir>`: output folder
- `--summary-sheet`: create `sheet.png` contact sheet
- `--summary-sheet-cols <n>`: contact sheet columns (default: auto)
- `--summary-gif`: create `preview.gif`
- `--summary-gif-width <px>`: GIF max width (default: `640`)

### UI mockups (annotations)

Create lightweight UI mockups by drawing arrows, rectangles, and text on a capture using a JSON spec. Output images + spec + metadata are saved together under `.seer/mockup/`.

### Excalidraw wireframes (NL → `.excalidraw`)

Generate an Excalidraw scene file from a simple, structured natural-language prompt. By default, it will use a bundled UI component library (if present) to render nicer headers/inputs/buttons/tabs automatically.

Docs: `docs/excalidraw-wireframing.md`

### Visual diff loop

Maintain baselines and compare current UI to previous snapshots with diffs and JSON reports. Useful for quick visual regressions or confirming UI changes.

### Organized artifacts

Every mockup run stores capture, spec, output, and metadata, plus `latest-*` convenience copies per app slug for fast access.

Examples:
```bash
bash skills/seer/scripts/capture_app_window.sh
bash skills/seer/scripts/capture_app_window.sh /tmp/promptlight.png "Promptlight"
bash skills/seer/scripts/capture_app_window.sh --help
bash skills/seer/scripts/record_app_window.sh --duration 3 --frames --fps 20
bash skills/seer/scripts/record_app_window.sh --duration 3 --summary --summary-mode scene --summary-max 24 --summary-sheet --summary-gif
bash skills/seer/scripts/extract_frames.sh /tmp/recording.mov --fps 20
bash skills/seer/scripts/summarize_video.sh /tmp/recording.mov --mode scene --sheet --gif
bash skills/seer/scripts/type_into_app.sh --app "Promptlight" --text "hello" --enter
bash skills/seer/scripts/type_into_app.sh --app "Promptlight" --click-rel 120,180 --text "hello"
bash skills/seer/scripts/type_into_app.sh --text "hello" --no-activate
bash skills/seer/scripts/type_into_app.sh --bundle-id com.example.app --text -
bash skills/seer/scripts/mockup_ui.sh --spec spec.json
bash skills/seer/scripts/mockup_ui.sh --spec spec.json --json
python3 skills/seer/scripts/annotate_image.py input.png output.png --spec spec.json
python3 skills/seer/scripts/annotate_image.py --spec-help
```

Mockup spec example (supports global defaults + auto-scale for visibility):
```json
{
  "defaults": {
    "auto_scale": true,
    "outline": true,
    "text_bg": "rgba(0,0,0,0.6)"
  },
  "annotations": [
    {"type": "spotlight", "x": 110, "y": 70, "w": 190, "h": 60, "radius": 10},
    {"type": "rect", "x": 120, "y": 80, "w": 160, "h": 40, "color": "#FF3B30", "width": 3},
    {"type": "arrow", "x1": 60, "y1": 140, "x2": 120, "y2": 100, "color": "#0A84FF", "width": 3},
    {"type": "text", "x": 130, "y": 90, "text": "Add button", "color": "#FFFFFF", "size": 14}
  ]
}
```

Auto-fit rect/spotlight bounds (optional):
```json
{
  "annotations": [
    {
      "type": "rect",
      "x": 80,
      "y": 1600,
      "w": 1000,
      "h": 600,
      "color": "#FF9F0A",
      "fit": "luma"
    },
    {
      "type": "rect",
      "x": 40,
      "y": 2600,
      "w": 1240,
      "h": 170,
      "color": "#FF9F0A",
      "fit": {"mode": "color", "color": "#CCB590", "tolerance": 18, "pad": 6}
    }
  ]
}
```
Notes:
- Auto-fit is **enabled by default** for rect/spotlight. Disable with `"fit": false` on an annotation or `"auto_fit": false` in defaults.
- `fit` searches within the provided `x/y/w/h` region and adjusts the rect bounds to the detected pixels.
- If the detected area is smaller than the original region, auto-fit recenters the rect/spotlight on the detected pixels while keeping the original size.
- `fit: "luma"` finds dark (or light) pixels by threshold (default `160`). Use `{"target":"light"}` for light text.
- `fit: {"mode":"color"}` matches a target color with `tolerance` (default `18`). Use `pad` to expand the result.
- Optional defaults: `fit_mode`, `fit_threshold`, `fit_target`, `fit_tolerance`, `fit_color`, `fit_pad`, `fit_min_pixels`, `fit_min_coverage` (default `0.6`).

Auto-anchor arrows/text (optional):
```json
{
  "annotations": [
    {"type": "rect", "id": "cta", "x": 40, "y": 2600, "w": 1240, "h": 170, "color": "#FF9F0A"},
    {"type": "arrow", "from": "cta", "from_pos": "top", "to": "nearest", "to_pos": "left"},
    {"type": "text", "text": "CTA needs more contrast", "anchor": "cta", "anchor_pos": "top", "anchor_offset": [0, -8]}
  ]
}
```
Notes:
- `anchor` (text) and `from`/`to` (arrow) accept `"nearest"`, an `id`, or an `index`.
- Positions: `center`, `top`, `bottom`, `left`, `right`, `top_left`, `top_right`, `bottom_left`, `bottom_right`.

Output layout (default under `.seer/`):
- `capture/` window screenshots
- `record/` window recordings + extracted frame folders
- `mockup/` annotated mockups + their capture/spec/meta (also writes `latest-*` convenience copies)
- `excalidraw/` generated `.excalidraw` scenes (also writes `latest-*.excalidraw`)
- `loop/` visual regression loop storage (baselines/latest/history/diffs/reports)

## Examples (prompts)

- "Check the layout of the app and suggest UI fixes."
- "Redesign this screen; take a screenshot first."
- "Is the spacing on this window consistent?"

## Permissions

- macOS Screen Recording + Accessibility for terminal
- Automation (System Events) required for `type_into_app.sh`

## Troubleshooting

- `error: window not found`: app not running, wrong process name, or no visible window.
- Empty/black image: Screen Recording not granted to terminal.
- Wrong window: pass exact process name (e.g. "Promptlight").
