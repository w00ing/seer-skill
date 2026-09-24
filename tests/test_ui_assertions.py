import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/seer/scripts"
sys.path.insert(0, str(SCRIPTS))
import ui_assertions


def element(node_id="n1", *, role="AXStaticText", name=None, value=None,
            enabled=None, confidence=None, bounds=None):
    return {"id": node_id, "role": role, "name": name, "value": value,
            "bounds": bounds, "enabled": enabled, "confidence": confidence}


def snapshot(elements=(), *, source="accessibility", complete=True, issues=None,
             window_id=123, query=None, artifacts=None):
    return {"schema_version": 1, "operation": "inspect", "status": "pass",
            "source": source, "window_id": window_id, "complete": complete,
            "issues": [] if issues is None else issues, "elements": list(elements),
            "query": {"element_count": len(elements)} if query is None else query,
            "artifacts": {"snapshot": "/tmp/inspect.json"} if artifacts is None else artifacts}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class InspectionError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class UIAssertionsTests(unittest.TestCase):
    def test_accessibility_text_presence_and_absence_use_individual_actual_fields(self):
        data = snapshot([element("a", name="Save"), element("b", value="Changes")])
        present = ui_assertions.evaluate(data, text="Save")
        self.assertEqual(present["status"], "pass")
        self.assertEqual(present["evidence"]["matched_node_ids"], ["a"])
        self.assertEqual(present["evidence"]["matched_texts"], ["Save"])
        absent = ui_assertions.evaluate(data, text_absent="Save changes", match="exact")
        self.assertEqual(absent["status"], "pass")
        cross_node = ui_assertions.evaluate(data, text="Save Changes")
        self.assertEqual(cross_node["status"], "fail")
        self.assertEqual(cross_node["reason"], "text_not_found")

    def test_accessibility_negative_match_and_empty_or_incomplete_trees(self):
        data = snapshot([element(name="Continue")])
        self.assertEqual(ui_assertions.evaluate(data, text_absent="Continue")["reason"], "text_present")
        empty = ui_assertions.evaluate(snapshot([]), text_absent="secret")
        self.assertEqual(empty["status"], "error")
        self.assertEqual(empty["error"]["code"], "insufficient_evidence")
        partial = ui_assertions.evaluate(snapshot([element(name="secret")], complete=False), text="secret")
        self.assertEqual(partial["error"]["code"], "insufficient_evidence")
        secure = ui_assertions.evaluate(snapshot([element(name="public")], issues=[{"code": "secure_field"}]),
                                        text_absent="password")
        self.assertEqual(secure["error"]["code"], "insufficient_evidence")

    def test_malformed_snapshot_is_an_operational_error(self):
        malformed = snapshot([element()])
        malformed["elements"][0]["confidence"] = 1.1
        result = ui_assertions.evaluate(malformed, text="anything")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "invalid_subprocess_output")
        self.assertEqual(ui_assertions.evaluate({"status": "pass"}, text="x")["error"]["code"],
                         "invalid_subprocess_output")
        scalar_value = ui_assertions.evaluate(snapshot([
            element("checkbox", role="AXCheckBox", name="Updates", value=True)
        ]), text="Updates")
        self.assertEqual(scalar_value["status"], "pass")

    def test_accessibility_control_exact_name_role_and_unknown_state(self):
        control = element("button-1", role="AXButton", name="Save", enabled=True)
        data = snapshot([control])
        result = ui_assertions.evaluate(data, enabled="Save", role="AXButton")
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["evidence"]["enabled"])
        self.assertEqual(result["evidence"]["matched_texts"], ["Save"])
        missing = ui_assertions.evaluate(snapshot([control]), disabled="save", role="AXButton")
        self.assertEqual(missing["status"], "fail")
        self.assertEqual(missing["reason"], "control_not_found")
        ambiguous = ui_assertions.evaluate(snapshot([
            control, element("button-2", role="AXButton", name="Save", enabled=False)
        ]), enabled="Save", role="AXButton")
        self.assertEqual(ambiguous["error"]["code"], "selector_ambiguous")
        unknown = ui_assertions.evaluate(snapshot([
            element("button-3", role="AXButton", name="Save", enabled=None)
        ]), enabled="Save")
        self.assertEqual(unknown["error"]["code"], "insufficient_evidence")
        label = element("label", role="AXStaticText", name="Save", enabled=True)
        for options in ({}, {"role": "AXStaticText"}):
            non_control = ui_assertions.evaluate(snapshot([label]), enabled="Save", **options)
            self.assertEqual(non_control["error"]["code"], "insufficient_evidence")

    def test_control_missing_from_uninformative_tree_is_insufficient(self):
        result = ui_assertions.evaluate(snapshot([element("n", role=None)]), enabled="Save")
        self.assertEqual(result["error"]["code"], "insufficient_evidence")

    def test_ocr_presence_requires_confidence_and_carries_bounds(self):
        bounds = {"x": 2.5, "y": 3, "width": 40, "height": 12}
        hit = element("ocr-1", role=None, name="Continue", confidence=0.92, bounds=bounds)
        result = ui_assertions.evaluate(snapshot([hit], source="ocr"), text="Continue")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["evidence"]["confidence"], 0.92)
        self.assertEqual(result["evidence"]["bounds"], [{"node_id": "ocr-1", "bounds": bounds}])
        weak = element("ocr-2", role=None, name="Continue", confidence=0.4, bounds=bounds)
        uncertain = ui_assertions.evaluate(snapshot([weak], source="ocr"), text="Continue")
        self.assertEqual(uncertain["error"]["code"], "insufficient_evidence")
        no_observations = ui_assertions.evaluate(snapshot([], source="ocr"), text="Continue")
        self.assertEqual(no_observations["error"]["code"], "insufficient_evidence")

    def test_ocr_cannot_prove_absence_and_requires_bounds(self):
        bounds = {"x": 0, "y": 0, "width": 8, "height": 9}
        high = element("ocr-1", role=None, name="Other", confidence=0.99, bounds=bounds)
        absent = ui_assertions.evaluate(snapshot([high], source="ocr"), text_absent="secret")
        self.assertEqual(absent["status"], "error")
        self.assertEqual(absent["error"]["code"], "insufficient_evidence")
        self.assertIn("cannot prove", absent["evidence"]["basis"])
        present = ui_assertions.evaluate(snapshot([element("ocr-2", role=None, name="secret",
                                                            confidence=0.99, bounds=bounds)], source="ocr"),
                                        text_absent="secret")
        self.assertEqual(present["status"], "fail")
        malformed = ui_assertions.evaluate(snapshot([element("ocr-3", role=None, name="secret",
                                                              confidence=0.99)], source="ocr"), text="secret")
        self.assertEqual(malformed["error"]["code"], "invalid_subprocess_output")
        control = ui_assertions.evaluate(snapshot([high], source="ocr"), enabled="Other")
        self.assertEqual(control["error"]["code"], "source_unsupported")

    def test_exact_condition_validation(self):
        for kwargs in ({}, {"text": "a", "text_absent": "b"}, {"text": "  "},
                       {"enabled": "Save", "role": "  "}, {"text": "a", "match": "prefix"}):
            with self.subTest(kwargs=kwargs):
                result = ui_assertions.evaluate(snapshot([element(name="a")]), **kwargs)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error"]["code"], "invalid_arguments")

    def run_wait(self, samples, *, timeout=1.0, interval=0.1, source="ax"):
        clock = FakeClock()
        calls = []

        def inspect(window_id, *, source, region, timeout):
            self.assertEqual(window_id, 123)
            self.assertGreater(timeout, 0)
            calls.append(timeout)
            sample = samples[min(len(calls) - 1, len(samples) - 1)]
            if isinstance(sample, BaseException):
                raise sample
            return sample

        with patch.object(ui_assertions.time, "monotonic", side_effect=clock.monotonic), \
                patch.object(ui_assertions.time, "sleep", side_effect=clock.sleep), \
                patch.object(ui_assertions, "_load_inspector", return_value=inspect):
            payload, code = ui_assertions.wait_for_condition(
                123, timeout=timeout, interval=interval, source=source, text="Ready")
        return payload, code, calls, clock

    def test_wait_transitions_from_nonmatch_to_pass(self):
        payload, code, calls, clock = self.run_wait([
            snapshot([element(name="Loading")]), snapshot([element(name="Ready")])
        ])
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["samples"], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(clock.now, 0.1)
        self.assertEqual(payload["last_assertion"]["status"], "pass")
        self.assertEqual(payload["matched_basis"], "accessibility_tree_text_fields")
        self.assertEqual(payload["artifacts"]["snapshot"], "/tmp/inspect.json")

    def test_wait_retries_only_valid_condition_failures_until_timeout(self):
        payload, code, calls, clock = self.run_wait([snapshot([element(name="Loading")])],
                                                    timeout=0.25, interval=0.1)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["reason"], "timeout")
        self.assertGreaterEqual(payload["samples"], 2)
        self.assertEqual(payload["last_assertion"]["status"], "fail")
        self.assertLessEqual(clock.now, 0.25)
        self.assertTrue(all(0 < remaining <= 0.25 for remaining in calls))

    def test_wait_inspection_timeout_and_operational_errors_are_not_condition_timeouts(self):
        for exc in (InspectionError("query_timeout", "read exceeded remaining budget"),
                    InspectionError("permission_denied", "accessibility permission is unavailable")):
            with self.subTest(code=exc.code):
                payload, code, _calls, _clock = self.run_wait([exc], timeout=1)
                self.assertEqual(code, 2)
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["error"]["code"], exc.code)
                self.assertNotEqual(payload.get("reason"), "timeout")

    def test_wait_rejects_snapshot_for_a_different_window(self):
        payload, code, _calls, _clock = self.run_wait([
            snapshot([element(name="Ready")], window_id=456)
        ])
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"]["code"], "invalid_subprocess_output")

    def test_wait_preserves_failed_query_details_without_relabeling_last_snapshot(self):
        inspection_error = InspectionError(
            "query_timeout", "read exceeded remaining budget",
            {"report": "/tmp/failed-report.json",
             "artifacts": {"report": "/tmp/failed-report.json", "current": "/tmp/failed.png"}},
        )
        payload, code, _calls, _clock = self.run_wait([
            snapshot([element(name="Loading")]), inspection_error,
        ], timeout=1, interval=0.1)
        self.assertEqual(code, 2)
        self.assertEqual(payload["last_assertion"]["status"], "fail")
        self.assertEqual(payload["artifacts"]["snapshot"], "/tmp/inspect.json")
        self.assertEqual(payload["query_failure"]["sample"], 2)
        self.assertEqual(payload["query_failure"]["details"]["report"], "/tmp/failed-report.json")
        self.assertEqual(payload["failed_query_artifacts"]["current"], "/tmp/failed.png")


if __name__ == "__main__":
    unittest.main()
