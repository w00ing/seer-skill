# Excalidraw wireframing tool

## What it does
Transforms a structured, natural‑language prompt into an Excalidraw scene (`.excalidraw`). The generator prefers Excalidraw library components (headers, inputs, buttons, tabs, etc.) and falls back to primitives when no good library match exists.

Primary entry point:
- `skills/seer/scripts/excalidraw_from_text.py`

Supplementary:
- `skills/seer/scripts/generate_wireframe_suite.py` (generates a component coverage suite + manifest)

## Input format
A simple, indented prompt with `screen:` boundaries and component types:

```
screen: Home
  header: Home
  card: Welcome banner
  input: Search
  list: Trending, New releases, Recommended
  button: Explore

screen: Settings
  header: Settings
  section: Account
  list: Profile, Security, Notifications
  section: Support
  tabs: Help | Contact | About
  button: Log out
```

Supported component types (default mapping):
- `screen`, `header`, `tabs`, `section`, `text`, `input`, `button`, `dropdown`, `textarea`, `checkbox`, `radio`, `toggle`, `chips`, `card`, `list`, `image`, `divider`, `footer`
- `lib:` / `library:` to request a specific library item (name + optional labels)

## Output
- Default output folder: `.seer/excalidraw/`
- Filenames: `nl-<slug>-<timestamp>-<pid>-<rand>.excalidraw`
- Convenience copy: `latest-<slug>.excalidraw`

`--json` mode prints metadata:
- `output_path`, `latest_path`
- `library_used` (elements total + by component)
- `screens` (name + count)

## Library usage
The generator loads a bundled Excalidraw library and attempts to map common UI components to library elements:
- Default library path (preferred): `skills/seer/assets/excalidraw/wireframe-ui-kit.excalidrawlib`
- Fallback: `skills/seer/assets/excalidraw/basic-ux-wireframing-elements.excalidrawlib`

Mapping logic lives in:
- `DEFAULT_LIBRARY_COMPONENT_QUERIES` and `_pick_library_item_for_component` inside `excalidraw_from_text.py`

Library elements are tagged for inspection:
- `customData.seerSource = "library"`
- `customData.seerLabel = <component>`

### Forcing a specific library item
Use `lib:` to explicitly pull by name (or partial name):

```
screen: Library sanity
  lib: navigation bar | Library sanity
  lib: tabs | One | Two | Three
  lib: dropdown | Country
```

## Layout + rendering notes
- Multiple screens are laid out horizontally with a fixed gap.
- Screen content uses a consistent inner padding and column layout.
- When a library component is inserted, the generator:
  - disables grid snapping for those elements
  - scales the group to the target bounds (`_fit_group_to_bounds`) to prevent overlap across screens
  - rewrites label text where possible (headers, tabs, placeholders, etc.)

These changes fix prior issues with button text alignment, tabs spacing, and cross‑screen overlap.

## CLI usage

Basic:
```
python3 skills/seer/scripts/excalidraw_from_text.py --name home < prompt.txt
```

With options:
```
python3 skills/seer/scripts/excalidraw_from_text.py \
  --name settings \
  --preset mobile \
  --theme classic \
  --fidelity medium \
  --library skills/seer/assets/excalidraw/wireframe-ui-kit.excalidrawlib \
  --json < prompt.txt
```

Disable library usage:
```
python3 skills/seer/scripts/excalidraw_from_text.py --no-library < prompt.txt
```

Generate the component test suite:
```
python3 skills/seer/scripts/generate_wireframe_suite.py
```

## Where we left off
- Verified output visually in `excalidraw.com` by loading `.seer/excalidraw/latest-layout-fix-test.excalidraw` via devtools‑injected file input.
- Confirmed layout fixes: no screen overlap, centered button labels, tabs aligned, and library elements are in use.
- Confirmed library usage stats (45 library elements across headers/inputs/buttons/tabs/etc.).

## Known gaps / next steps
- Auto‑center the canvas on load (set `appState.scrollX/scrollY` to fit bounds).
- Expand library mapping coverage and refine label rewriting for niche components.
- Add a quick CLI flag to emit a “centered” appState for easier preview.
- Optionally add a small regression test that checks `library_used.elements_total > 0` for suite outputs.
