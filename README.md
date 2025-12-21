# seer-skill

Visual feedback capture skill for macOS app windows.

[![release](https://img.shields.io/github/v/release/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/releases)
[![license](https://img.shields.io/github/license/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/blob/main/LICENSE)

## Support

- macOS only

## Demo

<video src="assets/seer-demo.mov" controls muted playsinline></video>

## Install

Codex (skill-installer UI):
- Run `$skill-installer`
- Ask: install GitHub repo `w00ing/seer-skill` path `seer`

Claude Code (plugin):
- `/plugin marketplace add w00ing/seer-skill`
- `/plugin install seer-skill@seer`

Manual (Codex):
```bash
mkdir -p ~/.codex/skills
git clone https://github.com/w00ing/seer-skill.git /tmp/seer-skill
rsync -a /tmp/seer-skill/seer/ ~/.codex/skills/seer/
```

Manual (Claude Code):
```bash
mkdir -p ~/.claude/skills
git clone https://github.com/w00ing/seer-skill.git /tmp/seer-skill
rsync -a /tmp/seer-skill/seer/ ~/.claude/skills/seer/
```

## Use

- Skill name: `seer`
- Script: `seer/scripts/capture_app_window.sh`
- Default output: `/tmp/seer/app-window-shot-YYYYMMDD-HHMMSS-<pid>-<rand>.png`
- Set `SEER_TMP_DIR` to change default output dir

Examples:
```bash
bash seer/scripts/capture_app_window.sh
bash seer/scripts/capture_app_window.sh /tmp/promptlight.png "Promptlight"
bash seer/scripts/capture_app_window.sh --help
```

## Examples (prompts)

- "Check the layout of the app and suggest UI fixes."
- "Redesign this screen; take a screenshot first."
- "Is the spacing on this window consistent?"

## Permissions

- macOS Screen Recording + Accessibility for terminal

## Troubleshooting

- `error: window not found`: app not running, wrong process name, or no visible window.
- Empty/black image: Screen Recording not granted to terminal.
- Wrong window: pass exact process name (e.g. "Promptlight").
