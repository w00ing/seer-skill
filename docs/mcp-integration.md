# MCP host integration

Seer can expose its existing CLI to Codex and Claude Code as an optional local stdio MCP server. The host starts the server process for one target project; the examples below keep the host configuration temporary. The Python MCP adapter requires Python 3.10 or newer. The existing CLI still supports Python 3.9.

## Prepare a clone or installed skill

Set `SKILL_ROOT` to the directory containing `scripts/seer_mcp.py` and `scripts/requirements-mcp.txt`. For a checkout, that is `.../seer-skill/skills/seer`; for an installed skill, use its installed `skills/seer` directory. Claude plugin installation paths vary, so use the directory that contains the installed script.

Set `PROJECT_ROOT` to the app or repository whose `.seer/` data Seer should use, and choose an absolute Python 3.10+ interpreter:

```bash
SKILL_ROOT="/absolute/path/to/seer-skill/skills/seer" # or installed skills/seer
PROJECT_ROOT="/absolute/path/to/target-project"
PYTHON310="/absolute/path/to/python3.11"

MCP_VENV="$PROJECT_ROOT/.local/seer-mcp-venv"
"$PYTHON310" -m venv "$MCP_VENV"
"$MCP_VENV/bin/python" -m pip install -r "$SKILL_ROOT/scripts/requirements-mcp.txt"
MCP_PYTHON="$MCP_VENV/bin/python"
MCP_SCRIPT="$SKILL_ROOT/scripts/seer_mcp.py"
```

Keep `.local/seer-mcp-venv` out of version control. Use the same `PROJECT_ROOT`, `MCP_PYTHON`, and `MCP_SCRIPT` values for either host. Each server launch passes the project root explicitly, so it does not depend on the MCP process's working directory.

The adapter prepends its Python environment to the CLI subprocess's `PATH` so the scripts use that environment's Pillow. Other environment settings are inherited: existing `SEER_OUT_DIR` or `SEER_LOOP_DIR` overrides still apply. Unset those before launch when you want the project's default `.seer/` paths. The project root is a working directory, not a filesystem sandbox; explicitly supplied paths can point elsewhere.

## Codex: one-run configuration

Codex supports stdio servers through `config.toml`; `-c` applies configuration overrides for a single CLI invocation. This example also skips the saved user config while retaining Codex authentication. It starts a read-only discovery/tool-call smoke check; run it from a trusted project directory.

```bash
codex exec -C "$PROJECT_ROOT" --ignore-user-config \
  -c "mcp_servers.seer.command=\"$MCP_PYTHON\"" \
  -c "mcp_servers.seer.args=[\"$MCP_SCRIPT\",\"--project-root\",\"$PROJECT_ROOT\",\"--command-timeout\",\"90\"]" \
  -c "mcp_servers.seer.cwd=\"$PROJECT_ROOT\"" \
  -c 'mcp_servers.seer.startup_timeout_sec=20' \
  -c 'mcp_servers.seer.tool_timeout_sec=120' \
  -c 'mcp_servers.seer.required=true' \
  "Call seer_help with command 'verify', then call seer_run with arguments ['doctor', '--json']. Report both results. Do not capture or change baselines."
```

The two timeout values are seconds. The per-server `tool_timeout_sec` gives the host 120 seconds; Seer's `--command-timeout` defaults to 60 seconds and is set to 90 here for longer OCR or Accessibility queries. Keep any CLI query `--timeout` below the server command timeout. Codex may also load trusted project or managed configuration; inspect `/mcp` if other servers appear. Do not use `codex mcp add` for this temporary evaluation because it writes saved configuration.

## Claude Code: temporary JSON configuration

Claude Code accepts a JSON file with `--mcp-config`; `--strict-mcp-config` limits the session to servers from that file. The temporary file below is removed when the shell exits.

```bash
MCP_CONFIG="$(mktemp)"
trap 'rm -f "$MCP_CONFIG"' EXIT

"$MCP_PYTHON" - "$MCP_CONFIG" "$MCP_PYTHON" "$MCP_SCRIPT" "$PROJECT_ROOT" <<'PY'
import json
import sys

config_path, python, script, project_root = sys.argv[1:]
with open(config_path, "w", encoding="utf-8") as config_file:
    json.dump({
        "mcpServers": {
            "seer": {
                "command": python,
                "args": [script, "--project-root", project_root,
                         "--command-timeout", "90"],
                "timeout": 120000,
            }
        }
    }, config_file)
PY

cd "$PROJECT_ROOT"
MCP_TIMEOUT=20000 claude --mcp-config "$MCP_CONFIG" --strict-mcp-config \
  "Call seer_help with command 'verify', then call seer_run with arguments ['doctor', '--json']. Report both results. Do not capture or change baselines."
```

`MCP_TIMEOUT` is the startup timeout in milliseconds; `timeout` in the JSON is the per-tool-call timeout, also in milliseconds. Claude Code versions before 2.1.246 can still wait for approval of project `.mcp.json` servers that strict mode does not load. If that affects a session, update Claude Code or run the evaluation from a separate trusted directory while keeping the explicit `--project-root` argument.

Keep another option after `--mcp-config "$MCP_CONFIG"`: that option accepts multiple values, so a prompt placed immediately after its filename can be interpreted as another configuration. Model-backed evaluation also requires a valid host login. The [v0.8 validation record](v0.8-validation.md) distinguishes configuration checks from completed host tool calls.

## Tool use and baseline approval

The server exposes `seer_help({"command":"verify"})` and `seer_run({"arguments":[...]})`. `seer_run` accepts argv for `doctor`, `windows`, `capture`, `verify`, `inspect`, `assert`, and `wait`, and returns the Seer CLI JSON in `structuredContent` and text. Check the returned `status`: `fail` and `needs_baseline` are valid Seer outcomes, not MCP transport errors. An MCP `isError` result means the command itself had an operational error.

If `verify` returns `needs_baseline`, inspect the current image and ask the user before creating or replacing a baseline. The MCP server rejects `--create-baseline` and `--update-baseline`; after explicit approval, perform that action with the Seer CLI, then use `seer_run` to verify it. Seer does not infer approval from a missing baseline or a passing tool call.

The host starts a local process and needs the same macOS Screen Recording and Accessibility access as the Seer CLI. Keep the target app visible and consult the [agent workflow](seer-agent-loop.md) for evidence limits.

The current Claude reference also describes negotiation for revision `2026-07-28` and MCP channel servers. Seer does not use channels, so this integration does not set `MCP_PROTOCOL_NEGOTIATION`; check host/SDK compatibility when changing versions.

## Host references

- [Codex MCP configuration](https://developers.openai.com/codex/mcp)
- [Claude Code MCP configuration](https://code.claude.com/docs/en/mcp)
