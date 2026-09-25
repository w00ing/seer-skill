import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "seer" / "scripts"
CLI = SCRIPTS / "seer"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class EvidenceToolsTests(unittest.TestCase):
    def run_cli(self, *arguments, env=None):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, arguments)],
            cwd=ROOT,
            env=env or os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=5,
        )

    def write_result(self, path, status, **extra):
        result = {"schema_version": 1, "operation": "verify", "status": status, **extra}
        path.write_text(json.dumps(result), encoding="utf-8")
        return result

    def make_bundle(self, root, status="fail"):
        bundle = root / "bundle"
        bundle.mkdir(parents=True)
        report = {
            "schema_version": 1,
            "operation": "verify",
            "status": status,
            "pixels_changed": 2,
            "pixels_total": 16,
            "percent_changed": 12.5,
            "thresholds": {"max_diff_percent": 0},
            "comparison_options": {"resize": False, "ignore_rects": [], "max_diff_percent": 0},
            "artifacts": {"diff": "/private/fixture/diff.png"},
        }
        if status == "error":
            report["error"] = {"code": "comparison_error", "message": "comparison failed"}
        contents = {
            "baseline": ("baseline.png", b"baseline bytes"),
            "current": ("current.png", b"current bytes"),
            "report": ("report.json", json.dumps(report).encode("utf-8")),
        }
        files = {}
        paths = {}
        for role, (name, data) in contents.items():
            (bundle / name).write_bytes(data)
            files[role] = {"path": name, "sha256": sha256(data), "size_bytes": len(data)}
            paths[role] = name
        manifest = {
            "schema_version": 1,
            "run_id": "fixture-run",
            "operation": "verify",
            "status": status,
            "paths": paths,
            "files": files,
            "images": {
                "baseline": {"path": "baseline.png", "sha256": files["baseline"]["sha256"]},
                "current": {"path": "current.png", "sha256": files["current"]["sha256"]},
            },
            "comparison": {"status": status, "error": report.get("error")},
        }
        manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        (bundle / "manifest.json").write_bytes(manifest_bytes)
        (bundle / "private-notes.txt").write_text("must not be exported", encoding="utf-8")
        return bundle, manifest, manifest_bytes

    def assert_error(self, result, code):
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["operation"], "report")
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"]["code"], code)
        self.assertIn("recovery", payload)
        return payload

    def test_reports_preserve_pass_fail_and_error_source_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = (
                ("pass", {"percent_changed": 0.0}, 0),
                ("fail", {"percent_changed": 12.5}, 1),
                ("error", {"error": {"code": "comparison_error", "message": "bad <script>alert(1)</script>\n[click](javascript:run)"}}, 2),
            )
            for status, extra, _unused_exit in cases:
                with self.subTest(status=status):
                    source = root / f"{status}.json"
                    report = root / f"{status}.md"
                    self.write_result(source, status, **extra)
                    result = self.run_cli("report", source, "--out", report, "--json")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["status"], "pass")
                    self.assertEqual(payload["source_status"], status)
                    self.assertEqual(payload["source_operation"], "verify")
                    self.assertEqual(payload["artifacts"]["report"], str(report.resolve()))
                    summary = report.read_text(encoding="utf-8")
                    self.assertIn(f"Source verdict: {status}", summary)
                    if status == "error":
                        self.assertIn("comparison\\_error", summary)
                        self.assertIn("Error:", summary)
                        self.assertNotIn("<script>", summary)
                        self.assertNotIn("[click]", summary)
                        self.assertNotIn("javascript:run)", summary)

    def test_bundle_export_contains_manifest_and_only_verified_declared_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, _manifest, manifest_bytes = self.make_bundle(root)
            report = root / "summary.md"
            archive_path = root / "evidence.zip"
            result = self.run_cli("report", bundle, "--out", report, "--export", archive_path, "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["source_status"], "fail")
            self.assertEqual(payload["source_operation"], "verify")
            self.assertEqual(payload["artifacts"]["export"], str(archive_path.resolve()))
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "manifest.json", "baseline.png", "current.png", "report.json",
                })
                self.assertEqual(archive.read("manifest.json"), manifest_bytes)
                self.assertEqual(archive.read("current.png"), b"current bytes")
            self.assertNotIn("private-notes", report.read_text(encoding="utf-8"))

    def test_bundle_checksum_path_traversal_and_symlink_failures_do_not_write_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, manifest, _ = self.make_bundle(root)
            (bundle / "current.png").write_bytes(b"tampered bytes")
            out = root / "tampered.md"
            self.assert_error(self.run_cli("report", bundle, "--out", out, "--json"), "evidence_tampered")
            self.assertFalse(out.exists())

            bundle, manifest, _ = self.make_bundle(root / "traversal")
            manifest["files"]["baseline"]["path"] = "../outside.png"
            manifest["paths"]["baseline"] = "../outside.png"
            (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            traversal_out = root / "traversal.md"
            self.assert_error(self.run_cli("report", bundle, "--out", traversal_out, "--json"), "unsafe_path")
            self.assertFalse(traversal_out.exists())

            if hasattr(os, "symlink"):
                symlink_bundle, _symlink_manifest, _ = self.make_bundle(root / "symlink")
                outside = root / "outside.png"
                outside.write_bytes(b"baseline bytes")
                (symlink_bundle / "baseline.png").unlink()
                (symlink_bundle / "baseline.png").symlink_to(outside)
                symlink_out = root / "symlink.md"
                self.assert_error(self.run_cli("report", symlink_bundle, "--out", symlink_out, "--json"), "unsafe_path")
                self.assertFalse(symlink_out.exists())

    def test_malformed_manifest_verdict_returns_json_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, manifest, _ = self.make_bundle(root)
            manifest["status"] = ["fail"]
            (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assert_error(self.run_cli("report", bundle, "--out", root / "malformed.md", "--json"), "invalid_evidence")

    def test_existing_outputs_are_preserved_and_json_export_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "result.json"
            self.write_result(source, "pass")
            existing = root / "summary.md"
            existing.write_text("keep me", encoding="utf-8")
            self.assert_error(self.run_cli("report", source, "--out", existing, "--json"), "output_exists")
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")

            self.assert_error(
                self.run_cli("report", source, "--out", root / "ignored.md", "--export", root / "export.zip", "--json"),
                "invalid_arguments",
            )
            self.assertFalse((root / "ignored.md").exists())

            bundle, _manifest, _bytes = self.make_bundle(root / "archive")
            zip_path = root / "already.zip"
            zip_path.write_bytes(b"keep zip")
            summary = root / "rollback.md"
            self.assert_error(
                self.run_cli("report", bundle, "--out", summary, "--export", zip_path, "--json"),
                "output_exists",
            )
            self.assertFalse(summary.exists())
            self.assertEqual(zip_path.read_bytes(), b"keep zip")

    def test_fifo_inputs_and_bundle_entries_fail_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("named pipes are unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo_input = root / "result.pipe"
            os.mkfifo(fifo_input)
            result = self.run_cli("report", fifo_input, "--out", root / "fifo.md", "--json")
            self.assert_error(result, "invalid_arguments")

            bundle, manifest, _ = self.make_bundle(root / "bundle-case")
            baseline = bundle / "baseline.png"
            baseline.unlink()
            os.mkfifo(baseline)
            empty_hash = sha256(b"")
            manifest["files"]["baseline"].update({"sha256": empty_hash, "size_bytes": 0})
            manifest["images"]["baseline"]["sha256"] = empty_hash
            (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            bundle_result = self.run_cli("report", bundle, "--out", root / "fifo-bundle.md", "--json")
            self.assert_error(bundle_result, "unsafe_path")

    def test_cyclic_output_path_is_a_json_error_without_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, _, _ = self.make_bundle(root)
            (root / "cycle").symlink_to("cycle")
            summary = root / "summary.md"
            result = self.run_cli("report", bundle, "--out", summary,
                                  "--export", root / "cycle" / "evidence.zip")
            self.assert_error(result, "filesystem_error")
            self.assertFalse(summary.exists())

    def test_deep_json_and_invalid_unicode_return_json_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            source.write_text('{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}')
            self.assert_error(self.run_cli("report", source, "--out", root / "deep.md"), "invalid_evidence")
            self.write_result(source, "error", error={"code": "invalid_arguments", "message": "\ud800"})
            self.assert_error(self.run_cli("report", source, "--out", root / "unicode.md"), "invalid_evidence")
            self.assertFalse((root / "unicode.md").exists())

    def test_export_size_limit_rejects_before_buffering_or_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, manifest, _ = self.make_bundle(root)
            manifest["files"]["baseline"]["size_bytes"] = 129 * 1024 * 1024
            (bundle / "manifest.json").write_text(json.dumps(manifest))
            result = self.run_cli("report", bundle, "--out", root / "summary.md",
                                  "--export", root / "archive.zip")
            self.assert_error(result, "evidence_too_large")
            self.assertFalse((root / "summary.md").exists())
            self.assertFalse((root / "archive.zip").exists())

    def test_diagnostics_exposes_only_allowlisted_nonpersonal_data(self):
        environment = os.environ.copy()
        environment["SEER_TEST_PRIVATE_VALUE"] = "env-sentinel-5264"
        result = self.run_cli("diagnostics", "--json", env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["operation"], "diagnostics")
        self.assertIn("seer_version", payload)
        self.assertEqual(set(payload["diagnostics"]["dependencies"]), {"pillow", "mcp", "jsonschema"})
        self.assertEqual(set(payload["diagnostics"]["tools"]), {"ffmpeg", "ffprobe"})
        serialized = json.dumps(payload)
        self.assertNotIn("env-sentinel-5264", serialized)
        for forbidden in ("hostname", "window", "capture", "authorization", "token", "password"):
            self.assertNotIn(forbidden, serialized.lower())


if __name__ == "__main__":
    unittest.main()
