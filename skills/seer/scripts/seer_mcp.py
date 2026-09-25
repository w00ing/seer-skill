#!/usr/bin/env python3
"""Expose the Seer CLI as a small local MCP stdio server."""

import argparse
import asyncio
import importlib.machinery
import importlib.util
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anyio
import jsonschema
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from result_guidance import error_payload


SCRIPTS = Path(__file__).resolve().parent
CLI_PATH = SCRIPTS / "seer"
OPERATIONS = ("doctor", "windows", "capture", "verify", "inspect", "assert", "wait")
OUTPUT_LIMIT = 8 * 1024 * 1024
STDERR_LIMIT = 64 * 1024
TERMINATE_GRACE_SECONDS = 1.0

RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "arguments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 128,
            "items": {"type": "string", "maxLength": 4096},
        }
    },
    "required": ["arguments"],
    "additionalProperties": False,
}
HELP_SCHEMA = {
    "type": "object",
    "properties": {"command": {"type": "string", "enum": ["", *OPERATIONS]}},
    "additionalProperties": False,
}
jsonschema.Draft202012Validator.check_schema(RUN_SCHEMA)
jsonschema.Draft202012Validator.check_schema(HELP_SCHEMA)
RUN_VALIDATOR = jsonschema.Draft202012Validator(RUN_SCHEMA)
HELP_VALIDATOR = jsonschema.Draft202012Validator(HELP_SCHEMA)


_loader = importlib.machinery.SourceFileLoader("seer_mcp_cli", str(CLI_PATH))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
if _spec is None:
    raise RuntimeError("could not load the Seer CLI parser")
CLI = importlib.util.module_from_spec(_spec)
_loader.exec_module(CLI)


def _tool_error(operation: str, code: str, message: str) -> CallToolResult:
    payload = error_payload(operation, code, message)
    return CallToolResult(
        content=[TextContent(text=json.dumps(payload, separators=(",", ":")))],
        structuredContent=payload,
        isError=True,
    )


def _tool_result(payload: Dict[str, Any], text: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(text=text)],
        structuredContent=payload,
        isError=payload.get("status") == "error",
    )


def _has_help_request(arguments: List[str]) -> bool:
    return any(token in ("-h", "--help") or token.startswith("--help=") for token in arguments)


def _parse_cli_arguments(arguments: List[str]) -> Optional[argparse.Namespace]:
    try:
        return CLI.parser().parse_args(arguments)
    except CLI.CLIUsageError:
        # Let the real CLI produce its canonical machine-readable usage error.
        return None
    except SystemExit:
        # Help is filtered before parsing; never allow argparse to exit the server.
        return None


async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> Tuple[bytes, bool]:
    kept = bytearray()
    exceeded = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return bytes(kept), exceeded
        remaining = limit - len(kept)
        if remaining > 0:
            kept.extend(chunk[:remaining])
        if len(chunk) > remaining:
            exceeded = True


def _signal_group(pid: int, signum: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(pid, signum)
        elif signum == signal.SIGTERM:
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        # The process handle fallback still ensures the owned CLI is reaped.
        pass


def _group_exists(pid: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


async def _terminate_owned_process(process: asyncio.subprocess.Process) -> None:
    """Stop the CLI's owned process group and always reap the CLI child."""
    _signal_group(process.pid, signal.SIGTERM)
    waiter = asyncio.create_task(process.wait())
    deadline = asyncio.get_running_loop().time() + TERMINATE_GRACE_SECONDS
    while os.name == "posix" and _group_exists(process.pid):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        if process.returncode is None:
            try:
                await asyncio.wait_for(asyncio.shield(waiter), min(0.05, remaining))
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(min(0.05, remaining))
    if _group_exists(process.pid):
        _signal_group(process.pid, signal.SIGKILL)
    elif process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await waiter


def _schedule_late_spawn_cleanup(task: asyncio.Task) -> None:
    """If spawn completes after cancellation, terminate the process it created."""
    def cleanup(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        try:
            process = done.result()
        except BaseException:
            return
        asyncio.create_task(_terminate_owned_process(process))

    task.add_done_callback(cleanup)


async def _run_cli_process(arguments: List[str], project_root: Path, timeout: float) -> Tuple[bytes, int, bool]:
    started = time.monotonic()
    environment = os.environ.copy()
    python_dir = str(Path(sys.executable).parent)
    environment["PATH"] = python_dir + os.pathsep + environment.get("PATH", "")
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            str(CLI_PATH),
            *arguments,
            cwd=str(project_root),
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    )
    try:
        process = await asyncio.wait_for(asyncio.shield(spawn), timeout=timeout)
    except asyncio.TimeoutError:
        _schedule_late_spawn_cleanup(spawn)
        raise
    except asyncio.CancelledError:
        _schedule_late_spawn_cleanup(spawn)
        raise

    stdout_reader = asyncio.create_task(_read_bounded(process.stdout, OUTPUT_LIMIT))
    stderr_reader = asyncio.create_task(_read_bounded(process.stderr, STDERR_LIMIT))
    collected = asyncio.gather(stdout_reader, stderr_reader, process.wait())
    remaining = timeout - (time.monotonic() - started)
    try:
        if remaining <= 0:
            raise asyncio.TimeoutError
        (stdout, overflow), _, _ = await asyncio.wait_for(asyncio.shield(collected), timeout=remaining)
    except asyncio.TimeoutError:
        with anyio.CancelScope(shield=True):
            await _terminate_owned_process(process)
            await asyncio.gather(collected, return_exceptions=True)
        raise
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            await _terminate_owned_process(process)
            await asyncio.gather(collected, return_exceptions=True)
        raise
    return stdout, process.returncode if process.returncode is not None else 2, overflow


def _validated_cli_payload(stdout: bytes, exit_code: int, command: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        output = stdout.decode("utf-8", errors="strict")
        start = len(output) - len(output.lstrip())
        payload, end = json.JSONDecoder().raw_decode(output, start)
        if output[end:].strip():
            raise ValueError
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
        return None, None
    if payload.get("operation") != command:
        return None, None
    status = payload.get("status")
    if not isinstance(status, str):
        return None, None
    expected_exit = {"pass": 0, "fail": 1, "error": 2, "needs_baseline": 3}.get(status)
    if expected_exit is None or expected_exit != exit_code:
        return None, None
    if status == "error":
        error = payload.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
            return None, None
    return payload, output[start:end]


def _cli_help(command: str) -> str:
    root = CLI.parser()
    if not command:
        return root.format_help()
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[command].format_help()
    raise RuntimeError("the Seer CLI command help is unavailable")


async def _call_seer_run(tool_arguments: Any, project_root: Path, timeout: float) -> CallToolResult:
    invalid = next(iter(RUN_VALIDATOR.iter_errors(tool_arguments)), None)
    if invalid is not None:
        return _tool_error("seer_run", "mcp_invalid_arguments", "arguments must be a non-empty list of up to 128 strings")
    arguments = tool_arguments["arguments"]
    if any("\x00" in item for item in arguments):
        return _tool_error("seer_run", "mcp_invalid_arguments", "arguments cannot contain NUL characters")
    command = arguments[0]
    if command not in OPERATIONS:
        return _tool_error("seer_run", "mcp_invalid_arguments", "command must be one of the documented Seer operations")
    if _has_help_request(arguments):
        return _tool_error("seer_run", "mcp_invalid_arguments", "use seer_help to read CLI help")
    parsed = _parse_cli_arguments(arguments)
    if parsed is not None and (getattr(parsed, "create_baseline", False) or getattr(parsed, "update_baseline", False)):
        return _tool_error(
            "verify",
            "baseline_approval_required",
            "MCP cannot create or replace approved baselines; ask the user to approve the baseline, then use the Seer CLI explicitly.",
        )

    try:
        stdout, exit_code, overflow = await _run_cli_process(arguments, project_root, timeout)
    except asyncio.TimeoutError:
        payload = error_payload("seer_run", "mcp_command_timeout", "Seer exceeded the configured command timeout.")
        return _tool_result(payload, json.dumps(payload, separators=(",", ":")))
    except OSError:
        payload = error_payload("seer_run", "mcp_cli_unavailable", "The Seer CLI could not be started.")
        return _tool_result(payload, json.dumps(payload, separators=(",", ":")))
    if overflow:
        payload = error_payload("seer_run", "mcp_invalid_output", "The Seer CLI response exceeded the output size limit.")
        return _tool_result(payload, json.dumps(payload, separators=(",", ":")))
    payload, text = _validated_cli_payload(stdout, exit_code, command)
    if payload is None or text is None:
        payload = error_payload("seer_run", "mcp_invalid_output", "The Seer CLI returned invalid or inconsistent JSON output.")
        return _tool_result(payload, json.dumps(payload, separators=(",", ":")))
    return _tool_result(payload, text)


async def _call_seer_help(tool_arguments: Any) -> CallToolResult:
    invalid = next(iter(HELP_VALIDATOR.iter_errors(tool_arguments)), None)
    if invalid is not None:
        return _tool_error("seer_help", "mcp_invalid_arguments", "command must be empty or a documented Seer operation")
    command = tool_arguments.get("command", "")
    help_text = _cli_help(command or "")
    payload = {"command": command or "", "help": help_text}
    return _tool_result(payload, help_text)


def build_server(project_root: Path, timeout: float) -> Server:
    async def list_tools(_context: Any, _params: Any) -> ListToolsResult:
        annotations = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
        return ListToolsResult(
            tools=[
                Tool(
                    name="seer_run",
                    title="Run Seer",
                    description=(
                        "Run an allowlisted Seer CLI operation against local macOS windows. Requested evidence "
                        "may be written to local project paths. Baseline creation and replacement require explicit CLI use."
                    ),
                    inputSchema=RUN_SCHEMA,
                    annotations=annotations,
                ),
                Tool(
                    name="seer_help",
                    title="Read Seer help",
                    description="Read the Seer CLI help or help for one supported operation.",
                    inputSchema=HELP_SCHEMA,
                    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
                ),
            ]
        )

    async def call_tool(_context: Any, params: CallToolRequestParams) -> CallToolResult:
        arguments = params.arguments if params.arguments is not None else {}
        if params.name == "seer_run":
            return await _call_seer_run(arguments, project_root, timeout)
        if params.name == "seer_help":
            return await _call_seer_help(arguments)
        return _tool_error("mcp", "mcp_unknown_tool", "The requested Seer MCP tool is unavailable.")

    return Server("seer", version="0.8.0", on_list_tools=list_tools, on_call_tool=call_tool)


def _positive_finite(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def _existing_project_root(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise argparse.ArgumentTypeError("must be an absolute path to an existing directory")
    return path.resolve()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Run the local Seer MCP stdio server.", allow_abbrev=False)
    root.add_argument("--project-root", required=True, type=_existing_project_root,
                      help="absolute existing project directory used as the Seer CLI working directory")
    root.add_argument("--command-timeout", type=_positive_finite, default=60.0,
                      help="overall timeout in seconds for each Seer CLI subprocess (default: 60)")
    return root


async def serve(project_root: Path, timeout: float) -> None:
    server = build_server(project_root, timeout)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: Optional[List[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        asyncio.run(serve(arguments.project_root, arguments.command_timeout))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
