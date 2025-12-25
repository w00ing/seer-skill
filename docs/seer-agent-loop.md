# Seer Agent Loop Ideas

## Goal
Turn Seer into a tight, repeatable visual-feedback loop for an agent: capture UI state, interpret it, act, and verify change.

## Core Loop
1. Capture window image (current UI state).
2. Inspect (OCR + lightweight parsing for key labels/errors).
3. Decide action (click/keypress/scroll).
4. Act (outside seer; e.g., UI automation layer).
5. Re-capture.
6. Diff vs previous state; stop when goal state is visible.

## Practical Upgrades
- State checkpointing: save each screenshot with a step id and a small JSON note (window name, timestamp, step, agent note).
- Visual diffing: highlight changes; gate re-analysis to changed regions.
- Region targeting: capture sub-rects (toolbar, modal) to reduce noise.
- OCR pass: extract on-screen text and feed it into the agent.
- Action coupling: record the action taken, then verify via re-capture.
- Failure heuristics: if no pixel change after action, re-plan (wrong window, modal blocking, action ignored).

## Minimal Agent-Friendly Workflow
- Capture: `capture_app_window.sh` -> image
- OCR + parse for key labels/errors
- Decide action
- Perform action
- Re-capture
- Diff and decide next step

## Script Ideas
- `scripts/capture_and_ocr.sh`: capture + OCR + JSON report
- `scripts/diff_last_two.sh`: compare last two captures, return changed?
- `scripts/capture_region.sh`: capture by rect or named region
- `assets/` overlays/templates for common UI elements

## Open Questions (for scoping)
- Primary use case: UI testing, autonomous browsing, IDE automation, or verification only?
- Should Seer also perform actions or stay capture-only?
- Should history be retained locally (artifact trail) or only latest frame?
