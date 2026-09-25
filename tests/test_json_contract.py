#!/usr/bin/env python3
"""Structural contract checks against real temporary Seer outputs."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "seer" / "scripts"
CLI = SCRIPTS / "seer"
JSONSCHEMA_AVAILABLE = importlib.util.find_spec("jsonschema") is not None
PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(JSONSCHEMA_AVAILABLE, "install jsonschema to validate Draft 2020-12 schemas")
@unittest.skipUnless(PIL_AVAILABLE, "install Pillow to generate synthetic comparison images")
class JsonContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import jsonschema

        cls.envelope_schema = json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
        cls.manifest_schema = json.loads((ROOT / "schemas" / "comparison-bundle-manifest.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(cls.envelope_schema)
        jsonschema.Draft202012Validator.check_schema(cls.manifest_schema)
        cls.envelope_validator = jsonschema.Draft202012Validator(cls.envelope_schema)
        cls.manifest_validator = jsonschema.Draft202012Validator(
            cls.manifest_schema,
            format_checker=jsonschema.FormatChecker(),
        )

    def assert_valid_envelope(self, value):
        errors = list(self.envelope_validator.iter_errors(value))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def assert_valid_manifest(self, value):
        errors = list(self.manifest_validator.iter_errors(value))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def _run(self, work: Path, loop: Path, current: Path, *extra: str):
        environment = os.environ.copy()
        environment["SEER_LOOP_DIR"] = str(loop)
        return subprocess.run(
            [sys.executable, str(CLI), "verify", str(current), "home", *extra, "--json"],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_command(self, work: Path, *arguments: str):
        return subprocess.run(
            [sys.executable, str(CLI), *arguments],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_real_cli_results_and_bundle_manifest_validate(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            loop = work / "loop"
            current = work / "current.png"
            Image.new("RGB", (4, 3), "white").save(current)

            created = self._run(work, loop, current, "--create-baseline")
            self.assertEqual(created.returncode, 0, created.stderr)
            created_payload = json.loads(created.stdout)
            self.assertEqual(created_payload["operation"], "baseline_create")
            self.assertEqual(created_payload["status"], "pass")
            self.assert_valid_envelope(created_payload)

            created_manifest_path = Path(created_payload["artifacts"]["manifest"])
            created_manifest = json.loads(created_manifest_path.read_text(encoding="utf-8"))
            self.assert_valid_manifest(created_manifest)
            self.assertEqual(set(created_manifest["paths"]), set(created_manifest["files"]))
            for role, entry in created_manifest["files"].items():
                self.assertEqual(created_manifest["paths"][role], entry["path"])

            Image.new("RGB", (4, 3), "black").save(current)
            failed = self._run(work, loop, current)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            failed_payload = json.loads(failed.stdout)
            self.assertEqual((failed_payload["operation"], failed_payload["status"]), ("verify", "fail"))
            self.assert_valid_envelope(failed_payload)
            failed_bundle = Path(failed_payload["artifacts"]["bundle"])
            failed_manifest = json.loads(Path(failed_payload["artifacts"]["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(failed_manifest["status"], "fail")
            self.assert_valid_manifest(failed_manifest)

            Image.new("RGB", (5, 3), "black").save(current)
            errored = self._run(work, loop, current)
            self.assertEqual(errored.returncode, 2, errored.stderr)
            errored_payload = json.loads(errored.stdout)
            self.assertEqual(errored_payload["status"], "error")
            self.assertEqual(errored_payload["error"]["code"], "image_size_mismatch")
            self.assert_valid_envelope(errored_payload)
            errored_manifest = json.loads(Path(errored_payload["artifacts"]["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(errored_manifest["status"], "error")
            self.assert_valid_manifest(errored_manifest)

            report_path = work / "summary.md"
            export_path = work / "bundle.zip"
            reported = self._run_command(
                work,
                "report",
                str(failed_bundle),
                "--out",
                str(report_path),
                "--export",
                str(export_path),
                "--json",
            )
            self.assertEqual(reported.returncode, 0, reported.stderr)
            report_payload = json.loads(reported.stdout)
            self.assertEqual(report_payload["status"], "pass")
            self.assertEqual(report_payload["source_operation"], "verify")
            self.assertEqual(report_payload["source_status"], "fail")
            self.assertEqual(Path(report_payload["artifacts"]["export"]), export_path.resolve())
            self.assertTrue(report_path.is_file())
            self.assertTrue(export_path.is_file())
            self.assert_valid_envelope(report_payload)

            capture_error_path = work / "capture-error.json"
            capture_error_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "operation": "capture",
                    "status": "error",
                    "error": {"code": "subprocess_failed", "message": "screen capture failed; review permission"},
                }),
                encoding="utf-8",
            )
            capture_summary = work / "capture-error.md"
            captured_error_report = self._run_command(
                work, "report", str(capture_error_path), "--out", str(capture_summary), "--json"
            )
            self.assertEqual(captured_error_report.returncode, 0, captured_error_report.stderr)
            capture_summary_payload = json.loads(captured_error_report.stdout)
            self.assertEqual(capture_summary_payload["status"], "pass")
            self.assertEqual(capture_summary_payload["source_operation"], "capture")
            self.assertEqual(capture_summary_payload["source_status"], "error")
            self.assert_valid_envelope(capture_summary_payload)

            diagnostics = self._run_command(work, "diagnostics", "--json")
            self.assertEqual(diagnostics.returncode, 0, diagnostics.stderr)
            self.assert_valid_envelope(json.loads(diagnostics.stdout))

            missing_baseline = self._run(work, loop, current, "--loop-dir", str(work / "missing-loop"))
            self.assertEqual(missing_baseline.returncode, 3, missing_baseline.stderr)
            missing_payload = json.loads(missing_baseline.stdout)
            self.assertEqual(missing_payload["status"], "needs_baseline")
            self.assert_valid_envelope(missing_payload)
            self.assertFalse((work / "missing-loop").exists())

    def test_envelope_rejects_bad_status_error_and_report_semantics(self):
        error_without_object = {"schema_version": 1, "operation": "verify", "status": "error"}
        self.assertFalse(self.envelope_validator.is_valid(error_without_object))

        verdict_with_error = {
            "schema_version": 1,
            "operation": "verify",
            "status": "pass",
            "error": {"code": "unexpected", "message": "must be absent"},
        }
        self.assertFalse(self.envelope_validator.is_valid(verdict_with_error))

        impossible_assertion = {"schema_version": 1, "operation": "assert", "status": "needs_baseline"}
        self.assertFalse(self.envelope_validator.is_valid(impossible_assertion))

        successful_adapter_error = {"schema_version": 1, "operation": "seer_run", "status": "pass"}
        self.assertFalse(self.envelope_validator.is_valid(successful_adapter_error))

        incomplete_report = {
            "schema_version": 1,
            "operation": "report",
            "status": "pass",
            "source_status": "error",
            "source_operation": "capture",
            "artifacts": {"report": "/tmp/report.md"},
        }
        self.assert_valid_envelope(incomplete_report)
        bad_source_status = copy.deepcopy(incomplete_report)
        bad_source_status["source_status"] = "fail"
        self.assertFalse(self.envelope_validator.is_valid(bad_source_status))
        incomplete_report["source_status"] = "needs_baseline"
        self.assertFalse(self.envelope_validator.is_valid(incomplete_report))

        diagnostics = {
            "schema_version": 1,
            "operation": "diagnostics",
            "status": "pass",
            "seer_version": "0.9.0",
            "diagnostics": {
                "platform": {"value": "darwin", "macos_release": "15.0", "architecture": "arm64"},
                "python": {"version": "3.13.0", "implementation": "CPython"},
                "dependencies": {
                    name: {"available": False, "version": None}
                    for name in ("pillow", "mcp", "jsonschema")
                },
                "tools": {"ffmpeg": {"available": False}, "ffprobe": {"available": False}},
            },
            "future_extension": {"kept": True},
        }
        self.assert_valid_envelope(diagnostics)

    def test_manifest_rejects_missing_digest_and_unsafe_file_names(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            loop = work / "loop"
            current = work / "current.png"
            Image.new("RGB", (2, 2), "white").save(current)
            completed = self._run(work, loop, current, "--create-baseline")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads(Path(json.loads(completed.stdout)["artifacts"]["manifest"]).read_text(encoding="utf-8"))

            missing_file_role = copy.deepcopy(manifest)
            del missing_file_role["files"]["current"]
            self.assertFalse(self.manifest_validator.is_valid(missing_file_role))

            malformed_digest = copy.deepcopy(manifest)
            malformed_digest["files"]["baseline"]["sha256"] = "not-a-sha256"
            self.assertFalse(self.manifest_validator.is_valid(malformed_digest))

            unsafe_path = copy.deepcopy(manifest)
            unsafe_path["files"]["baseline"]["path"] = "../outside.png"
            self.assertFalse(self.manifest_validator.is_valid(unsafe_path))

            status_mismatch = copy.deepcopy(manifest)
            status_mismatch["status"] = "pass"
            status_mismatch["comparison"]["status"] = "error"
            status_mismatch["comparison"]["error"] = {"code": "broken", "message": "not a pass"}
            self.assertFalse(self.manifest_validator.is_valid(status_mismatch))


if __name__ == "__main__":
    unittest.main()
