#!/usr/bin/env python3
"""Opt-in MCP/native smoke check; controls only SeerWindowFixture.

Install scripts/requirements-mcp.txt into an isolated Python 3.10+ environment.
Build tests/manual/window_fixture.swift as .seer/qa/SeerWindowFixture first.
Evidence stays under .seer/qa/v08-*; no real baseline is created or replaced.
"""
import asyncio
import hashlib
import json
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp import Client, StdioServerParameters
from verify_v07 import session_lock_state

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills/seer/scripts"


async def main():
    work = Path(tempfile.mkdtemp(prefix="v08-native-", dir=ROOT / ".seer/qa"))
    report = {"status": "error", "checks": [], "evidence": str(work)}
    fixture = None
    try:
        state, _ = session_lock_state()
        if state == "locked":
            raise RuntimeError("screen_locked; resume in the unlocked desktop")
        fixture = subprocess.Popen([str(ROOT / ".seer/qa/SeerWindowFixture")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)

        def acknowledge(expected):
            if not select.select([fixture.stdout], [], [], 10)[0]:
                raise RuntimeError("fixture acknowledgement timed out")
            if fixture.stdout.readline().strip() != expected:
                raise RuntimeError("unexpected fixture acknowledgement")

        acknowledge("ready")
        fixture.stdin.write("v07-start\n")
        fixture.stdin.flush()
        acknowledge("v07-start")
        env = dict(os.environ)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        env.pop("SEER_OUT_DIR", None)
        env.pop("SEER_LOOP_DIR", None)
        server = StdioServerParameters(command=sys.executable,
            args=[str(SCRIPTS / "seer_mcp.py"), "--project-root", str(work)], env=env)
        async with Client(server, read_timeout_seconds=90) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {"seer_run", "seer_help"}

            async def run(label, arguments, expected="pass", save=True):
                started = time.monotonic()
                result = await client.call_tool("seer_run", {"arguments": arguments})
                data = result.structured_content
                assert json.loads(result.content[0].text) == data
                assert result.is_error == (data["status"] == "error"), data
                assert data["status"] == expected, data
                entry = {"label": label, "elapsed_seconds": time.monotonic() - started,
                         "status": data["status"]}
                if save:
                    entry["result"] = data
                report["checks"].append(entry)
                return data

            windows = await run("window discovery", ["windows"], save=False)
            matches = [w for w in windows["windows"] if w["title"] == "Seer QA v0.7"
                       and w["process"] == "SeerWindowFixture"]
            assert len(matches) == 1, "fixture window must be unique"
            window_id = str(matches[0]["window_id"])
            capture = await run("exact fixture capture", ["capture", "--window-id", window_id])
            current = Path(capture["artifacts"]["current"])
            assert hashlib.sha256(current.read_bytes()).hexdigest() == capture["capture"]["image_sha256"]
            await run("AX text match", ["assert", "--window-id", window_id, "--text", "Seer Ready"])
            await run("AX mismatch remains fail", ["assert", "--window-id", window_id, "--text", "Definitely absent"], "fail")
            await run("semantic wait", ["wait", "--window-id", window_id, "--text", "Seer Ready", "--timeout", "5"])
            await run("missing baseline remains unapproved", ["verify", str(current), "not-approved"], "needs_baseline")
            assert not (work / ".seer/loop").exists()
            fixture.stdin.write("v07-close\n")
            fixture.stdin.flush()
            acknowledge("v07-close")
            await run("closed window is an error", ["inspect", "--window-id", window_id], "error")
            report["preview"] = str(current)
        report["status"] = "pass"
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        if fixture:
            if fixture.poll() is None:
                fixture.stdin.write("quit\n")
                fixture.stdin.flush()
            try:
                fixture.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                fixture.kill()
                fixture.communicate()
        path = work / "report.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": report["status"], "report": str(path), "preview": report.get("preview")}))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
