import contextlib
import importlib.machinery
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/seer/scripts"
sys.path.insert(0, str(SCRIPTS))
loader = importlib.machinery.SourceFileLoader("seer_ui_cli_test", str(SCRIPTS / "seer"))
spec = importlib.util.spec_from_loader(loader.name, loader)
CLI = importlib.util.module_from_spec(spec)
loader.exec_module(CLI)


class InspectionError(Exception):
    def __init__(self, code, message, details=None):
        self.code, self.message, self.details = code, message, details or {}


def snapshot():
    return {"schema_version": 1, "operation": "inspect", "status": "pass", "source": "accessibility",
            "window_id": 42, "complete": True, "issues": [], "artifacts": {"report": "/fixture/query.json"},
            "elements": [
                {"id": "0/1", "role": "AXStaticText", "name": None, "value": "Ready",
                 "enabled": None, "confidence": None, "bounds": {"x": 10, "y": 10, "width": 30, "height": 20}},
                {"id": "0/2", "role": "AXButton", "name": "Continue", "value": None,
                 "enabled": True, "confidence": None, "bounds": {"x": 10, "y": 50, "width": 80, "height": 20}},
            ]}


class UICommandTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock(return_value=snapshot())
        self.backend = types.ModuleType("ui_inspect")
        self.backend.inspect_ui = self.reader
        self.backend.InspectionError = InspectionError

    def invoke(self, *args):
        output, diagnostics = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, {"ui_inspect": self.backend}), \
                patch.object(sys, "argv", [str(SCRIPTS / "seer"), *args]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics):
            code = CLI.main()
        return code, json.loads(output.getvalue()), diagnostics.getvalue()

    def test_inspect_passes_window_region_source_and_timeout(self):
        code, payload, errors = self.invoke("inspect", "--window-id", "42", "--source", "ax",
                                             "--region", "1,2,30,40", "--timeout", "2")
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(payload["operation"], "inspect")
        self.reader.assert_called_once_with(42, source="ax", region=(1, 2, 30, 40), timeout=2)

    def test_inspection_default_allows_native_ocr_startup(self):
        for arguments in (("inspect",), ("assert", "--text", "Ready")):
            with self.subTest(arguments=arguments):
                self.invoke(*arguments, "--window-id", "42")
                self.assertEqual(self.reader.call_args.kwargs["timeout"], 30)

    def test_assert_exit_codes_follow_evidence(self):
        for condition, expected in ((["--text", "Ready"], 0), (["--text", "Missing"], 1),
                                    (["--text-absent", "Missing"], 0),
                                    (["--enabled", "Continue", "--role", "AXButton"], 0),
                                    (["--disabled", "Continue"], 1)):
            with self.subTest(condition=condition):
                code, payload, _ = self.invoke("assert", "--window-id", "42", *condition)
                self.assertEqual(code, expected, payload)
                self.assertEqual(payload["operation"], "assert")

    def test_query_failure_is_error_for_inspect_and_assert(self):
        self.reader.side_effect = InspectionError("accessibility_required", "AX unavailable", {"fixture": True})
        for command in (("inspect",), ("assert", "--text", "Ready")):
            with self.subTest(command=command):
                code, payload, diagnostics = self.invoke(*command, "--window-id", "42")
                self.assertEqual(code, 2)
                self.assertEqual(payload["error"]["code"], "accessibility_required")
                self.assertEqual(payload["operation"], command[0])
                self.assertIn("error:", diagnostics)

    def test_invalid_conditions_do_not_query_apps(self):
        cases = [
            ["assert", "--text", ""], ["assert", "--text", "Ready", "--enabled", "Continue"],
            ["assert", "--enabled", "Continue", "--source", "ocr"],
            ["assert", "--text", "Ready", "--min-confidence", "nan"],
            ["inspect", "--region", "0,0,0,20"],
            ["wait", "--stable", "--text", "Ready"],
            ["wait", "--text", "Ready", "--out", "ignored.png"],
        ]
        for args in cases:
            with self.subTest(args=args):
                code, payload, _ = self.invoke(*args, "--window-id", "42")
                self.assertEqual(code, 2)
                self.assertEqual(payload["error"]["code"], "invalid_arguments")
        self.reader.assert_not_called()

    def test_semantic_wait_uses_ui_evidence_and_preserves_query_errors(self):
        code, payload, diagnostics = self.invoke("wait", "--text", "Ready", "--window-id", "42",
                                                  "--timeout", "2", "--interval", "0.1")
        self.assertEqual((code, payload["operation"], payload["status"], diagnostics),
                         (0, "wait", "pass", ""))
        self.assertEqual(payload["last_assertion"]["source"], "accessibility")
        self.reader.side_effect = InspectionError("query_timeout", "Native query timed out")
        code, payload, diagnostics = self.invoke("wait", "--text", "Ready", "--window-id", "42")
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "query_timeout")
        self.assertIn("error:", diagnostics)


if __name__ == "__main__":
    unittest.main()
