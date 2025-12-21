# seer-skill

Visual feedback capture skill for macOS app windows.

[![release](https://img.shields.io/github/v/release/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/releases)

## Install

Codex (skill-installer UI):
- Run `$skill-installer`
- Ask: install GitHub repo `w00ing/seer-skill` path `seer`

Codex (CLI):
```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo w00ing/seer-skill --path seer
```

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
- Default output: `/tmp/app-window-shot-YYYYMMDD-HHMMSS-<pid>-<rand>.png`

Examples:
```bash
seer/scripts/capture_app_window.sh
seer/scripts/capture_app_window.sh /tmp/promptlight.png "Promptlight"
seer/scripts/capture_app_window.sh --help
```

## Permissions

- macOS Screen Recording + Accessibility for terminal
