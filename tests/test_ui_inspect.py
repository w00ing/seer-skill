import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/seer/scripts"
sys.path.insert(0, str(SCRIPTS))
import ui_assertions
import ui_inspect


def window_document(*, window_id=123, pid=321, bounds=None):
    return {
        "schema_version": 1,
        "status": "pass",
        "source": "window",
        "complete": True,
        "window": {
            "window_id": window_id,
            "pid": pid,
            "title": "Fixture",
            "bounds": bounds or {"x": 100, "y": 50, "width": 100, "height": 50},
        },
        "elements": [],
    }


def accessibility_document(elements, *, complete=True, issues=None):
    document = window_document()
    document.update({
        "source": "accessibility",
        "complete": complete,
        "issues": [] if issues is None else issues,
        "elements": elements,
    })
    return document


class UIInspectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        environment = patch.dict(os.environ, {"SEER_OUT_DIR": str(self.work)}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)

    def inspect_ax(self, document, *, region=None, after=None):
        after = after or window_document()
        with patch.object(ui_inspect, "_get_native_binary", return_value=self.work / "worker"), \
                patch.object(ui_inspect, "_run_native", side_effect=[document, after]):
            return ui_inspect.inspect_ui(123, source="ax", region=region)

    def fake_capture(self, window_id, destination, deadline, *, size=(200, 100)):
        self.assertEqual(window_id, 123)
        destination = Path(destination)
        Image.new("RGB", size, "white").save(destination)
        image_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
        metadata = Path(str(destination) + ".seer.json")
        metadata.write_text(json.dumps({"window_id": window_id, "image_sha256": image_hash}), encoding="utf-8")
        report = destination.parent / "capture_report.json"
        report.write_text(json.dumps({
            "schema_version": 1,
            "operation": "capture",
            "status": "pass",
            "artifacts": {"current": str(destination), "metadata": str(metadata)},
            "capture": {"window_id": window_id, "image_sha256": image_hash},
        }), encoding="utf-8")
        return {
            "current": destination,
            "metadata": metadata,
            "capture_report": report,
            "capture": {"window_id": window_id, "image_sha256": image_hash},
        }

    def test_region_filters_by_bounds_center_and_marks_unscopable_elements_incomplete(self):
        snapshot = accessibility_document([
            {"id": 1, "role": "AXWindow", "name": None, "value": None,
             "bounds": {"x": 0, "y": 0, "width": 100, "height": 50}, "enabled": None, "confidence": None},
            {"id": 2, "role": "AXButton", "name": "Save", "value": True,
             "bounds": {"x": 11, "y": 11, "width": 2, "height": 2}, "enabled": True, "confidence": None},
            {"id": 3, "role": "AXStaticText", "name": None, "value": "Outside",
             "bounds": {"x": 30, "y": 12, "width": 2, "height": 2}, "enabled": None, "confidence": None},
            {"id": 4, "role": None, "name": None, "value": "Unknown location",
             "bounds": None, "enabled": None, "confidence": None},
        ])
        result = self.inspect_ax(snapshot, region=(10, 10, 20, 20))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["source"], "accessibility")
        self.assertEqual(result["coordinate_space"], "window_points")
        self.assertEqual(result["region"], {"x": 10.0, "y": 10.0, "width": 20.0, "height": 20.0})
        self.assertIn("bounds center", result["region_semantics"])
        self.assertEqual([item["id"] for item in result["elements"]], [2])
        self.assertIs(result["elements"][0]["value"], True)
        self.assertFalse(result["complete"])
        self.assertEqual(result["issues"][-1]["code"], "region_scope_unknown")
        report = json.loads(Path(result["artifacts"]["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report, result)
        self.assertTrue(result["observed_at"].endswith("Z"))
        self.assertGreaterEqual(result["elapsed_seconds"], 0)

    def test_normalized_accessibility_snapshot_evaluates_with_real_assertion_worker(self):
        snapshot = accessibility_document([
            {"id": 8, "role": "AXCheckBox", "name": "Updates", "value": True,
             "bounds": {"x": 12, "y": 8, "width": 20, "height": 14}, "enabled": True, "confidence": None},
        ])
        result = self.inspect_ax(snapshot)
        assertion = ui_assertions.evaluate(result, text="Updates")
        self.assertEqual(assertion["status"], "pass")
        self.assertEqual(assertion["evidence"]["matched_node_ids"], [8])
        self.assertEqual(assertion["evidence"]["matched_texts"], ["Updates"])

    def test_incomplete_empty_tree_stays_incomplete_for_assertion_policy(self):
        result = self.inspect_ax(accessibility_document([], complete=False, issues=["node_limit_reached"]))
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["complete"])
        assertion = ui_assertions.evaluate(result, text_absent="secret")
        self.assertEqual(assertion["status"], "error")
        self.assertEqual(assertion["error"]["code"], "insufficient_evidence")

    def test_invalid_region_is_rejected_before_query(self):
        for region in ((-1, 0, 1, 1), (0, 0, 0, 1), (0, 0, float("nan"), 1), (0, 0, 1)):
            with self.subTest(region=region), patch.object(ui_inspect, "_get_native_binary") as build:
                with self.assertRaises(ui_inspect.InspectionError) as caught:
                    ui_inspect.inspect_ui(123, source="ax", region=region)
                self.assertEqual(caught.exception.code, "invalid_arguments")
                build.assert_not_called()

    def test_region_must_fit_window_and_failure_report_is_saved(self):
        document = accessibility_document([])
        with patch.object(ui_inspect, "_get_native_binary", return_value=self.work / "worker"), \
                patch.object(ui_inspect, "_run_native", return_value=document):
            with self.assertRaises(ui_inspect.InspectionError) as caught:
                ui_inspect.inspect_ui(123, source="ax", region=(90, 0, 11, 5))
        self.assertEqual(caught.exception.code, "invalid_arguments")
        report_path = Path(caught.exception.details["report"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["error"]["code"], "invalid_arguments")

    def test_malformed_accessibility_data_is_an_operational_error_with_report(self):
        document = accessibility_document([
            {"id": 1, "role": "AXButton", "name": "Save", "value": None,
             "bounds": {"x": "bad", "y": 0, "width": 5, "height": 5}, "enabled": True, "confidence": None},
        ])
        with patch.object(ui_inspect, "_get_native_binary", return_value=self.work / "worker"), \
                patch.object(ui_inspect, "_run_native", return_value=document):
            with self.assertRaises(ui_inspect.InspectionError) as caught:
                ui_inspect.inspect_ui(123, source="ax")
        self.assertEqual(caught.exception.code, "invalid_subprocess_output")
        report = json.loads(Path(caught.exception.details["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["error"]["code"], "invalid_subprocess_output")

    def test_window_pid_or_bounds_change_rejects_the_observation(self):
        snapshot = accessibility_document([])
        changed = window_document(bounds={"x": 101, "y": 50, "width": 100, "height": 50})
        with self.assertRaises(ui_inspect.InspectionError) as caught:
            self.inspect_ax(snapshot, after=changed)
        self.assertEqual(caught.exception.code, "window_changed")
        report = json.loads(Path(caught.exception.details["report"]).read_text(encoding="utf-8"))
        self.assertEqual(report["error"]["code"], "window_changed")

    def test_ocr_crops_and_maps_pixel_boxes_back_to_window_points(self):
        ocr = {
            "schema_version": 1,
            "status": "pass",
            "source": "ocr",
            "complete": True,
            "issues": [],
            "image_size": {"width": 40, "height": 20},
            "elements": [{
                "id": 7,
                "role": "text",
                "name": None,
                "value": "Continue",
                "bounds": {"x": 2, "y": 4, "width": 10, "height": 6},
                "enabled": None,
                "confidence": 0.93,
            }],
        }
        with patch.object(ui_inspect, "_get_native_binary", return_value=self.work / "worker"), \
                patch.object(ui_inspect, "_run_native", side_effect=[window_document(), ocr, window_document()]), \
                patch.object(ui_inspect, "_capture_exact_window", side_effect=self.fake_capture):
            result = ui_inspect.inspect_ui(123, source="ocr", region=(10, 5, 20, 10))
        element = result["elements"][0]
        self.assertEqual(element["id"], 7)
        self.assertEqual(element["bounds"], {"x": 11.0, "y": 7.0, "width": 5.0, "height": 3.0})
        self.assertEqual(element["confidence"], 0.93)
        crop = Path(result["artifacts"]["crop"])
        self.assertTrue(crop.is_file())
        with Image.open(crop) as image:
            self.assertEqual(image.size, (40, 20))
        self.assertTrue(Path(result["artifacts"]["current"]).is_file())
        self.assertTrue(Path(result["artifacts"]["metadata"]).is_file())
        self.assertTrue(Path(result["artifacts"]["capture_report"]).is_file())
        assertion = ui_assertions.evaluate(result, text="Continue")
        self.assertEqual(assertion["status"], "pass")
        self.assertEqual(assertion["evidence"]["matched_node_ids"], [7])

    def test_nonuniform_capture_geometry_is_rejected_and_capture_is_retained(self):
        ocr = {
            "schema_version": 1,
            "status": "pass",
            "source": "ocr",
            "complete": True,
            "issues": [],
            "image_size": {"width": 1, "height": 1},
            "elements": [],
        }
        with patch.object(ui_inspect, "_get_native_binary", return_value=self.work / "worker"), \
                patch.object(ui_inspect, "_run_native", side_effect=[window_document(), ocr, window_document()]), \
                patch.object(ui_inspect, "_capture_exact_window", side_effect=lambda wid, dest, deadline: self.fake_capture(wid, dest, deadline, size=(201, 100))):
            with self.assertRaises(ui_inspect.InspectionError) as caught:
                ui_inspect.inspect_ui(123, source="ocr")
        self.assertEqual(caught.exception.code, "capture_geometry_mismatch")
        artifacts = caught.exception.details["artifacts"]
        self.assertTrue(Path(artifacts["current"]).is_file())
        self.assertTrue(Path(artifacts["metadata"]).is_file())
        self.assertEqual(json.loads(Path(artifacts["report"]).read_text())["status"], "error")

    def test_ocr_missing_bounds_is_a_structured_error(self):
        document = {"image_size": {"width": 20, "height": 20}, "elements": [
            {"id": 1, "role": "text", "value": "Ready", "bounds": None, "confidence": 1.0}]}
        mapping = {"ocr_image_size": document["image_size"], "scale_x": 1, "scale_y": 1,
                   "crop_origin": {"x": 0, "y": 0}}
        with self.assertRaises(ui_inspect.InspectionError) as caught:
            ui_inspect._normalize_ocr_elements(document, mapping)
        self.assertEqual(caught.exception.code, "invalid_subprocess_output")

    def test_missing_pillow_is_reported_before_capture(self):
        with patch.object(ui_inspect, "_get_native_binary", return_value=self.work / "worker"), \
                patch.object(ui_inspect, "_run_native", return_value=window_document()), \
                patch.object(ui_inspect, "_load_pillow", side_effect=ui_inspect.InspectionError("dependency_missing", "Pillow missing")), \
                patch.object(ui_inspect, "_capture_exact_window") as capture:
            with self.assertRaises(ui_inspect.InspectionError) as caught:
                ui_inspect.inspect_ui(123, source="ocr")
        self.assertEqual(caught.exception.code, "dependency_missing")
        capture.assert_not_called()

    def test_native_cache_fingerprint_reuses_atomic_build(self):
        source = self.work / "worker.swift"
        source.write_text("print(\"fixture\")\n", encoding="utf-8")
        builds = []

        def run(command, deadline):
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, "Swift version fixture", "")
            builds.append(command)
            output = Path(command[-1])
            output.write_text("binary fixture", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(ui_inspect, "NATIVE_SOURCE", source), \
                patch.object(ui_inspect.shutil, "which", return_value="/usr/bin/xcrun"), \
                patch.object(ui_inspect, "_run_process", side_effect=run):
            first = ui_inspect._get_native_binary(time.monotonic() + 10, self.work / ".seer")
            second = ui_inspect._get_native_binary(time.monotonic() + 10, self.work / ".seer")
        self.assertEqual(first, second)
        self.assertTrue(first.is_file())
        self.assertTrue(os.access(first, os.X_OK))
        self.assertEqual(len(builds), 1)
        self.assertFalse(list((self.work / ".seer/cache").glob(".native-build-*")))

    def test_native_process_timeout_returns_structured_timeout(self):
        started = time.monotonic()
        with self.assertRaises(ui_inspect.InspectionError) as caught:
            ui_inspect._run_process(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                started + 0.05,
            )
        self.assertEqual(caught.exception.code, "query_timeout")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_native_error_and_malformed_worker_json_are_structured(self):
        error = subprocess.CompletedProcess(
            ["native", "ax"], 2,
            json.dumps({"schema_version": 1, "status": "error", "error": {"code": "accessibility_required", "message": "permission"}}),
            "",
        )
        with self.assertRaises(ui_inspect.InspectionError) as caught:
            ui_inspect._parse_worker_result(error, "ax")
        self.assertEqual(caught.exception.code, "accessibility_required")
        malformed = subprocess.CompletedProcess(["native", "ax"], 0, "not-json", "")
        with self.assertRaises(ui_inspect.InspectionError) as caught:
            ui_inspect._parse_worker_result(malformed, "ax")
        self.assertEqual(caught.exception.code, "invalid_subprocess_output")


if __name__ == "__main__":
    unittest.main()
