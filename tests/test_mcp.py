import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "seer" / "scripts"
MCP_SCRIPT = SCRIPTS / "seer_mcp.py"
CLI_SCRIPT = SCRIPTS / "seer"
SDK_AVAILABLE = importlib.util.find_spec("mcp") is not None
sys.path.insert(0, str(SCRIPTS))


@unittest.skipUnless(SDK_AVAILABLE, "install skills/seer/scripts/requirements-mcp.txt")
class MCPStdioTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.loop = self.project / "visual-loop"
        self.current = self.project / "current.png"
        Image.new("RGB", (4, 4), "blue").save(self.current)
        self.environment = os.environ.copy()
        self.environment["SEER_LOOP_DIR"] = str(self.loop)
        self.environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + self.environment.get("PATH", "")

    def _client_params(self):
        from mcp.client.stdio import StdioServerParameters

        return StdioServerParameters(
            command=sys.executable,
            args=[str(MCP_SCRIPT), "--project-root", str(self.project)],
            cwd=str(self.project),
            env=self.environment,
        )

    def _cli_result(self, *arguments):
        return subprocess.run(
            [sys.executable, str(CLI_SCRIPT), *arguments],
            cwd=self.project,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_legacy_stdio_preserves_cli_verdict_and_evidence(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import stdio_client
        from PIL import Image

        baseline_dir = self.loop / "baselines"
        baseline_dir.mkdir(parents=True)
        Image.new("RGB", (4, 4), "blue").save(baseline_dir / "approved.png")
        Image.new("RGB", (4, 4), "red").save(baseline_dir / "different.png")
        cases = [
            (("verify", str(self.current), "approved"), "pass", False),
            (("verify", str(self.current), "different"), "fail", False),
            (("verify", str(self.current), "not-created"), "needs_baseline", False),
        ]

        async def exercise():
            async with stdio_client(self._client_params()) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    for arguments, status, is_error in cases:
                        with self.subTest(arguments=arguments):
                            result = await session.call_tool("seer_run", {"arguments": list(arguments)})
                            cli = self._cli_result(*arguments)
                            self.assertEqual(cli.returncode, {"pass": 0, "fail": 1, "needs_baseline": 3}[status])
                            expected = json.loads(cli.stdout)
                            actual = result.structured_content
                            self.assertEqual(json.loads(result.content[0].text), actual)
                            self.assertEqual(result.content[0].text, json.dumps(actual, separators=(",", ":")))
                            self.assertEqual(actual["status"], status)
                            self.assertEqual(actual["operation"], expected["operation"])
                            self.assertEqual(actual.get("percent_changed"), expected.get("percent_changed"))
                            self.assertEqual(actual.get("options"), expected.get("options"))
                            if status == "needs_baseline":
                                self.assertEqual(actual, expected)
                                self.assertEqual(result.content[0].text, cli.stdout.strip())
                            else:
                                report_text = Path(actual["artifacts"]["report"]).read_text(encoding="utf-8")
                                self.assertEqual(json.loads(report_text), actual)
                                for artifact in actual["artifacts"].values():
                                    self.assertTrue(Path(artifact).exists(), artifact)
                            self.assertEqual(result.is_error, is_error)

        asyncio.run(exercise())

    def test_legacy_stdio_baseline_guard_and_canonical_argument_errors(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import stdio_client
        from PIL import Image

        baseline_dir = self.loop / "baselines"
        baseline_dir.mkdir(parents=True)
        existing = baseline_dir / "approved.png"
        Image.new("RGB", (4, 4), "red").save(existing)
        approved_bytes = existing.read_bytes()
        before = sorted(path.name for path in baseline_dir.iterdir())

        async def exercise():
            async with stdio_client(self._client_params()) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    blocked = await session.call_tool(
                        "seer_run",
                        {"arguments": ["verify", str(self.current), "new-baseline", "--create-baseline"]},
                    )
                    self.assertTrue(blocked.is_error)
                    self.assertEqual(blocked.structured_content["error"]["code"], "baseline_approval_required")
                    self.assertIn("approve", blocked.structured_content["recovery"]["next_action"])
                    self.assertFalse((baseline_dir / "new-baseline.png").exists())

                    replacement = await session.call_tool(
                        "seer_run", {"arguments": ["verify", str(self.current), "approved", "--update-baseline"]}
                    )
                    self.assertEqual(replacement.structured_content["error"]["code"], "baseline_approval_required")
                    self.assertEqual(existing.read_bytes(), approved_bytes)

                    abbreviated = await session.call_tool(
                        "seer_run",
                        {"arguments": ["verify", str(self.current), "new-baseline", "--create"]},
                    )
                    self.assertEqual(abbreviated.structured_content["error"]["code"], "invalid_arguments")
                    self.assertFalse((baseline_dir / "new-baseline.png").exists())

                    invalid = await session.call_tool(
                        "seer_run", {"arguments": ["assert", "--window-id", "not-an-id", "--text", "Ready"]}
                    )
                    cli = self._cli_result("assert", "--window-id", "not-an-id", "--text", "Ready")
                    self.assertEqual(invalid.structured_content, json.loads(cli.stdout))
                    self.assertEqual(invalid.structured_content["error"]["code"], "invalid_arguments")

                    extra = await session.call_tool(
                        "seer_run", {"arguments": ["doctor"], "create_baseline": True}
                    )
                    self.assertEqual(extra.structured_content["error"]["code"], "mcp_invalid_arguments")
                    self.assertEqual(sorted(path.name for path in baseline_dir.iterdir()), before)

        asyncio.run(exercise())

    def test_modern_stdio_envelope_reads_help(self):
        from mcp import types
        from mcp.client.stdio import stdio_client
        from mcp.shared.message import SessionMessage
        from mcp.types.version import LATEST_MODERN_VERSION

        async def exercise():
            async with stdio_client(self._client_params()) as (read_stream, write_stream):
                request = types.JSONRPCRequest(
                    jsonrpc="2.0",
                    id=1,
                    method="tools/call",
                    params={
                        "_meta": {
                            types.PROTOCOL_VERSION_META_KEY: LATEST_MODERN_VERSION,
                            types.CLIENT_CAPABILITIES_META_KEY: {},
                            types.CLIENT_INFO_META_KEY: {"name": "seer-mcp-test", "version": "1"},
                        },
                        "name": "seer_help",
                        "arguments": {"command": "assert"},
                    },
                )
                await write_stream.send(SessionMessage(request))
                response_message = await asyncio.wait_for(read_stream.receive(), timeout=5)
                self.assertIsInstance(response_message.message, types.JSONRPCResponse)
                response = types.JSONRPCResponse.model_validate(response_message.message)
                result = types.CallToolResult.model_validate(response.result)
                self.assertEqual(result.structured_content["command"], "assert")
                self.assertIn("Assert text", result.structured_content["help"])
                self.assertEqual(result.content[0].text, result.structured_content["help"])

        asyncio.run(exercise())

    def test_cli_timeout_and_cancellation_reap_owned_process(self):
        import seer_mcp
        import anyio

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pid_file = directory / "pid"
            fake_cli = directory / "sleeping_cli.py"
            fake_cli.write_text(
                "import os, pathlib, signal, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "pathlib.Path(os.environ['SEER_TEST_PID_FILE']).write_text(str(os.getpid()))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            self.environment["SEER_TEST_PID_FILE"] = str(pid_file)

            async def wait_for_pid():
                deadline = asyncio.get_running_loop().time() + 3
                while not pid_file.exists() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.01)
                self.assertTrue(pid_file.exists(), "test CLI did not start")
                return int(pid_file.read_text(encoding="utf-8"))

            async def cancel_run():
                task = asyncio.create_task(seer_mcp._run_cli_process(["doctor"], self.project, 10))
                pid = await wait_for_pid()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                return pid

            async def timeout_run():
                with self.assertRaises(asyncio.TimeoutError):
                    await seer_mcp._run_cli_process(["doctor"], self.project, 0.1)
                return int(pid_file.read_text(encoding="utf-8"))

            async def anyio_cancel_run():
                async with anyio.create_task_group() as group:
                    async def run_cli():
                        await seer_mcp._run_cli_process(["doctor"], self.project, 10)

                    group.start_soon(run_cli)
                    pid = await wait_for_pid()
                    group.cancel_scope.cancel()
                return pid

            def assert_gone(pid):
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        return
                    time.sleep(0.02)
                self.fail("owned CLI process remained alive after timeout/cancellation")

            with patch.object(seer_mcp, "CLI_PATH", fake_cli):
                with patch.dict(os.environ, self.environment):
                    pid = asyncio.run(cancel_run())
                assert_gone(pid)
                pid_file.unlink()
                with patch.dict(os.environ, self.environment):
                    pid = asyncio.run(timeout_run())
                assert_gone(pid)
                pid_file.unlink()
                with patch.dict(os.environ, self.environment):
                    pid = asyncio.run(anyio_cancel_run())
                assert_gone(pid)

    def test_malformed_child_output_is_an_adapter_error(self):
        import seer_mcp

        with tempfile.TemporaryDirectory() as tmp:
            fake_cli = Path(tmp) / "invalid_cli.py"
            fake_cli.write_text("print('not JSON')\n", encoding="utf-8")

            async def exercise():
                with patch.object(seer_mcp, "CLI_PATH", fake_cli):
                    result = await seer_mcp._call_seer_run({"arguments": ["doctor"]}, self.project, 2)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], "mcp_invalid_output")

            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
