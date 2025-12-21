# seer-skill

Visual feedback capture skill for macOS app windows.

[![release](https://img.shields.io/github/v/release/w00ing/seer-skill)](https://github.com/w00ing/seer-skill/releases)

## Install

Codex (skill-installer):
- Run `$skill-installer`
- Ask: install GitHub repo `w00ing/seer-skill` path `seer`

CLI:
```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo w00ing/seer-skill --path seer
```

Claude Code plugin:
- `/plugin marketplace add w00ing/seer-skill`
- `/plugin install seer-skill@seer`

## Use

- Skill name: `seer`
- Script: `seer/scripts/capture_app_window.sh`
- Default output: `/tmp/app-window-shot-YYYYMMDD-HHMMSS-<pid>-<rand>.png`

## Manual install

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/w00ing/seer-skill.git /tmp/seer-skill
rsync -a /tmp/seer-skill/seer/ ~/.codex/skills/seer/
```
