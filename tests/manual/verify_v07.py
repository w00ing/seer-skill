#!/usr/bin/env python3
"""Opt-in macOS QA for Seer's v0.7 native accessibility and OCR commands.

Build and run from the repository root:

    mkdir -p .seer/qa
    swiftc tests/manual/window_fixture.swift -o .seer/qa/SeerWindowFixture
    python3 tests/manual/verify_v07.py

The script controls only the local SeerWindowFixture, targets its exact window
ID, and writes reports and a fresh preview PNG under .seer/qa/v07-*. It does
not inspect other apps' content, request permissions, or create baselines. It
is intentionally not part of CI.
"""

import hashlib
import json
import platform
import select
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "skills/seer/scripts/seer"
FIXTURE = ROOT / ".seer/qa/SeerWindowFixture"
QA = ROOT / ".seer/qa"
WINDOW_TITLE = "Seer QA v0.7"
FIXTURE_PROCESS = "SeerWindowFixture"
SESSION_STATUS_SOURCE = """
import Foundation
import CoreGraphics

let session = CGSessionCopyCurrentDictionary() as? [String: Any] ?? [:]
if let locked = session["CGSSessionScreenIsLocked"] as? NSNumber {
    print(locked.boolValue ? "locked" : "unlocked")
} else {
    print("unknown")
}
"""


class PermissionUnavailable(RuntimeError):
    """An OS permission prevented an otherwise valid QA check."""


def payload(result):
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"Seer returned invalid JSON: {result.stdout!r}; {result.stderr.strip()}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("Seer returned a non-object JSON payload")
    return value


def session_lock_state():
    """Read the current macOS session lock flag without interacting with apps."""
    if sys.platform != "darwin":
        return "unsupported", "session lock preflight requires macOS"
    try:
        result = subprocess.run(
            ["swift", "-e", SESSION_STATUS_SOURCE],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", str(exc) or type(exc).__name__
    state = result.stdout.strip()
    if result.returncode != 0 or state not in {"locked", "unlocked", "unknown"}:
        return "unknown", result.stderr.strip() or result.stdout.strip()
    return state, result.stderr.strip()


def error_text(data, result):
    error = data.get("error")
    if isinstance(error, dict):
        details = " ".join(str(error.get(key, "")) for key in ("code", "message"))
    else:
        details = str(error or data.get("reason") or data.get("code") or "")
    return f"{details} {result.stderr}".lower()


def is_permission_error(data, result):
    message = error_text(data, result)
    markers = (
        "accessibility_required",
        "accessibility permission",
        "screen_recording",
        "screen recording",
        "screen capture permission",
        "not authorized",
        "not allowed",
        "permission denied",
    )
    return any(marker in message for marker in markers)


def cli(report, label, *args, timeout=30):
    result = subprocess.run(
        [sys.executable, str(CLI), *map(str, args), "--json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=ROOT,
    )
    data = payload(result)
    report["commands"][label] = {
        "args": list(map(str, args)),
        "returncode": result.returncode,
        "result": data,
        "stderr": result.stderr.strip(),
    }
    if result.returncode == 2 and is_permission_error(data, result):
        raise PermissionUnavailable(f"{label}: {error_text(data, result).strip()}")
    return result, data


def ensure_operation(result, data, *, code, status, label):
    if result.returncode != code or data.get("status") != status:
        raise AssertionError(
            f"{label}: expected exit {code} and status {status!r}; "
            f"got exit {result.returncode}, payload={data}"
        )


def error_code(data):
    error = data.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    return data.get("reason") or data.get("code")


def walk_records(value):
    """Yield AX/OCR element-like dictionaries from a nested response."""
    if isinstance(value, dict):
        if any(key in value for key in ("role", "ax_role", "confidence")) and any(
            key in value for key in ("name", "label", "title", "value", "text")
        ):
            yield value
        for child in value.values():
            yield from walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_records(child)


def text_fields(record):
    return [
        str(record[key])
        for key in ("name", "label", "title", "value", "text", "description")
        if isinstance(record.get(key), (str, int, float))
    ]


def contains_text(record, text):
    return any(text in candidate for candidate in text_fields(record))


def find_record(records, text):
    found = [record for record in records if contains_text(record, text)]
    if not found:
        raise AssertionError(f"no inspected element contains {text!r}: {records}")
    return found[0]


def rectangle(value):
    if isinstance(value, dict):
        x = value.get("x", value.get("X"))
        y = value.get("y", value.get("Y"))
        width = value.get("width", value.get("w", value.get("Width")))
        height = value.get("height", value.get("h", value.get("Height")))
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        x, y, width, height = value
    else:
        raise AssertionError(f"rectangle has an unsupported shape: {value!r}")
    try:
        values = tuple(float(item) for item in (x, y, width, height))
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"rectangle has non-numeric coordinates: {value!r}") from exc
    if not all(value == value for value in values) or values[2] <= 0 or values[3] <= 0:
        raise AssertionError(f"rectangle is invalid: {value!r}")
    return values


def local_rect(element, window_bounds):
    """Convert screen-space AX bounds to top-left window-local points if needed."""
    x, y, width, height = rectangle(element.get("bounds"))
    wx, wy, window_width, window_height = rectangle(window_bounds)
    if x >= wx - 1 and y >= wy - 1 and (x > window_width or y > window_height):
        x -= wx
        y -= wy
    if x < -1 or y < -1 or x + width > window_width + 1 or y + height > window_height + 1:
        raise AssertionError(
            f"AX bounds {element.get('bounds')!r} do not fit window bounds {window_bounds!r}"
        )
    return x, y, width, height


def role(element):
    return element.get("role", element.get("ax_role"))


def verify_fields(element, *, expected_role, label):
    if role(element) != expected_role:
        raise AssertionError(f"{label}: expected role {expected_role!r}, got {role(element)!r}")
    if not isinstance(element.get("name"), str) or not element["name"].strip():
        raise AssertionError(f"{label}: accessibility name is missing: {element}")
    if "value" not in element:
        raise AssertionError(f"{label}: accessibility value field is missing: {element}")
    rectangle(element.get("bounds"))
    if "enabled" not in element or (
        element["enabled"] is not None and not isinstance(element["enabled"], bool)
    ):
        raise AssertionError(f"{label}: enabled field is missing or invalid: {element}")


def assert_result(report, label, window_id, source, condition, *, expected_code, status="pass"):
    result, data = cli(
        report,
        label,
        "assert",
        "--window-id", window_id,
        "--source", source,
        *condition,
        timeout=30,
    )
    ensure_operation(result, data, code=expected_code, status=status, label=label)
    report["scenarios"][label] = {
        "check": "pass",
        "operation_status": status,
        "result": data,
    }
    print(f"PASS {label}", flush=True)
    return result, data


def snapshot(report):
    result, data = cli(report, "window_discovery", "windows", timeout=30)
    ensure_operation(result, data, code=0, status="pass", label="window discovery")
    matches = [
        item for item in data.get("windows", [])
        if item.get("process") == FIXTURE_PROCESS and item.get("title") == WINDOW_TITLE
    ]
    if len(matches) > 1:
        raise AssertionError(f"expected one fixture window titled {WINDOW_TITLE!r}, found {matches}")
    if not matches:
        return None
    item = matches[0]
    if not isinstance(item.get("window_id"), int):
        raise AssertionError(f"fixture window has no numeric window ID: {item}")
    rectangle(item.get("bounds"))
    return item


def wait_window(report, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = snapshot(report)
        if item:
            return item
        time.sleep(0.2)
    raise RuntimeError(f"fixture window {WINDOW_TITLE!r} did not appear")


def record(report, name, details):
    report["scenarios"][name] = details
    print(f"PASS {name}", flush=True)


def main():
    QA.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="v07-", dir=QA))
    swift_version = subprocess.run(
        ["swiftc", "--version"], capture_output=True, text=True, timeout=10, cwd=ROOT
    )
    report = {
        "date": datetime.now().astimezone().isoformat(),
        "machine_versions": {
            "platform": platform.platform(),
            "macos": platform.mac_ver()[0],
            "python": platform.python_version(),
            "swiftc": swift_version.stdout.strip() if swift_version.returncode == 0 else None,
            "seer_cli_sha256": hashlib.sha256(CLI.read_bytes()).hexdigest(),
            "fixture_source_sha256": hashlib.sha256(
                (ROOT / "tests/manual/window_fixture.swift").read_bytes()
            ).hexdigest(),
        },
        "output_directory": str(output),
        "fixture_commands": [
            "v07-start", "v07-loading", "v07-ready-delayed", "v07-close", "quit"
        ],
        "commands": {},
        "scenarios": {},
        "limits": [],
        "status": "error",
    }
    fixture = None

    lock_state, lock_detail = session_lock_state()
    report["preflight"] = {
        "check": "CGSessionCopyCurrentDictionary.CGSSessionScreenIsLocked",
        "state": lock_state,
        "detail": lock_detail,
    }
    if lock_state == "locked":
        report["status"] = "blocked"
        report["blocked_reason"] = "screen_locked"
        report["limits"].append("native fixture was not launched because the current session is locked")
        report_path = output / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(report_path)
        return 2

    def acknowledge(expected):
        ready, _, _ = select.select([fixture.stdout], [], [], 10)
        if not ready:
            raise RuntimeError(f"fixture did not acknowledge {expected!r}")
        actual = fixture.stdout.readline().strip()
        if actual != expected:
            raise RuntimeError(f"fixture acknowledgement mismatch: expected {expected!r}, got {actual!r}")

    def command(value):
        fixture.stdin.write(value + "\n")
        fixture.stdin.flush()
        acknowledge(value)

    try:
        if not FIXTURE.is_file():
            raise RuntimeError(f"fixture executable is missing; build it first: {FIXTURE}")
        fixture = subprocess.Popen(
            [str(FIXTURE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=ROOT,
        )
        acknowledge("ready")
        command("v07-start")
        window = wait_window(report)
        window_id = str(window["window_id"])
        report["fixture_window"] = window

        preview = output / "fixture.png"
        try:
            capture_result, capture_data = cli(
                report, "fresh_fixture_capture", "capture", "--window-id", window_id,
                "--out", preview, timeout=30,
            )
            ensure_operation(capture_result, capture_data, code=0, status="pass", label="fixture capture")
            if not preview.is_file():
                raise AssertionError(f"capture did not create the requested preview: {preview}")
            report["preview_png"] = str(preview)
            record(report, "fresh CLI PNG captured for visual inspection", {
                "path": str(preview), "window_id": window_id,
                "artifacts": capture_data.get("artifacts"),
            })
        except PermissionUnavailable as exc:
            report["limits"].append(f"fixture PNG capture was blocked by OS permission: {exc}")
            report["preview_png"] = None

        # First inspect may need to compile the native helper, so give it a
        # longer operation budget than later checks.
        inspect_result, inspect_data = cli(
            report, "ax_inspect", "inspect", "--window-id", window_id,
            "--source", "ax", "--timeout", "30", timeout=40,
        )
        ensure_operation(inspect_result, inspect_data, code=0, status="pass", label="AX inspect")
        ax_records = list(walk_records(inspect_data))
        status = find_record(ax_records, "Seer Ready")
        continue_button = find_record(ax_records, "Continue")
        unavailable_button = find_record(ax_records, "Unavailable")
        verify_fields(status, expected_role="AXStaticText", label="status label")
        verify_fields(continue_button, expected_role="AXButton", label="Continue button")
        verify_fields(unavailable_button, expected_role="AXButton", label="Unavailable button")
        if status.get("name") != "Status" or status.get("value") != "Seer Ready":
            raise AssertionError(f"status AX name/value were not preserved: {status}")
        if continue_button.get("enabled") is not True:
            raise AssertionError(f"Continue should be enabled: {continue_button}")
        if unavailable_button.get("enabled") is not False:
            raise AssertionError(f"Unavailable should be disabled: {unavailable_button}")
        if continue_button.get("name") != "Continue" or unavailable_button.get("name") != "Unavailable":
            raise AssertionError(f"button accessibility names are incorrect: {continue_button}, {unavailable_button}")
        if any(contains_text(item, "Canvas Evidence") for item in ax_records):
            raise AssertionError("custom-drawn canvas text unexpectedly appeared in the AX tree")
        record(report, "AX exposes the native status and button fields with correct roles", {
            "status": status,
            "continue": continue_button,
            "unavailable": unavailable_button,
            "custom_canvas_text_exposed": False,
        })

        assert_result(report, "AX text present passes", window_id, "ax",
                      ["--text", "Seer Ready", "--match", "exact"], expected_code=0)
        assert_result(report, "AX absent unknown text passes", window_id, "ax",
                      ["--text-absent", "No Such Fixture Label 837", "--match", "exact"], expected_code=0)
        assert_result(report, "AX text absent fails when present", window_id, "ax",
                      ["--text-absent", "Seer Ready", "--match", "exact"],
                      expected_code=1, status="fail")
        assert_result(report, "AX text present fails when missing", window_id, "ax",
                      ["--text", "Missing Fixture Label 837", "--match", "exact"],
                      expected_code=1, status="fail")
        assert_result(report, "enabled AX control passes", window_id, "ax",
                      ["--enabled", "Continue", "--role", "AXButton", "--match", "exact"],
                      expected_code=0)
        assert_result(report, "disabled AX control passes", window_id, "ax",
                      ["--disabled", "Unavailable", "--role", "AXButton", "--match", "exact"],
                      expected_code=0)
        assert_result(report, "disabled AX control fails enabled check", window_id, "ax",
                      ["--enabled", "Unavailable", "--role", "AXButton", "--match", "exact"],
                      expected_code=1, status="fail")
        assert_result(report, "enabled AX control fails disabled check", window_id, "ax",
                      ["--disabled", "Continue", "--role", "AXButton", "--match", "exact"],
                      expected_code=1, status="fail")

        # Pick a tiny region around the Continue button's inspected AX center.
        # The region is derived from returned element and window bounds and
        # should contain that button while excluding the known status label.
        bx, by, bw, bh = local_rect(continue_button, window["bounds"])
        region = f"{bx + bw / 2 - 2:.2f},{by + bh / 2 - 2:.2f},4,4"
        region_result, region_data = cli(
            report, "ax_window_local_region", "inspect", "--window-id", window_id,
            "--source", "ax", "--region", region, "--timeout", "15", timeout=30,
        )
        ensure_operation(region_result, region_data, code=0, status="pass", label="AX region inspect")
        region_records = list(walk_records(region_data))
        if not any(contains_text(item, "Continue") for item in region_records):
            raise AssertionError(f"derived button-center region missed Continue: {region_data}")
        if any(contains_text(item, "Seer Ready") for item in region_records):
            raise AssertionError(f"button-center region included the status label: {region_data}")
        record(report, "window-local region includes its derived button center only", {
            "region": region,
            "derived_from": continue_button.get("bounds"),
            "included_continue": True,
            "excluded_status": True,
            "result": region_data,
        })

        command("v07-loading")
        loading_result, loading_data = cli(
            report, "loading_command_assertion", "assert", "--window-id", window_id,
            "--source", "ax", "--text", "Loading", "--match", "exact", timeout=30,
        )
        ensure_operation(loading_result, loading_data, code=0, status="pass", label="loading command")
        record(report, "loading command exposes Loading", {"result": loading_data})

        command("v07-ready-delayed")
        loading_result, loading_data = cli(
            report, "loading_state_assertion", "assert", "--window-id", window_id,
            "--source", "ax", "--text", "Loading", "--match", "exact", timeout=30,
        )
        ensure_operation(loading_result, loading_data, code=0, status="pass", label="loading state")
        delayed_started = time.monotonic()
        wait_result, wait_data = cli(
            report, "delayed_text_wait", "wait", "--window-id", window_id,
            "--text", "Seer Ready", "--source", "ax", "--timeout", "10",
            "--interval", "0.25", timeout=20,
        )
        elapsed = time.monotonic() - delayed_started
        ensure_operation(wait_result, wait_data, code=0, status="pass", label="delayed text wait")
        if wait_data.get("samples", 0) < 2:
            raise AssertionError(f"delayed wait did not observe multiple states: {wait_data}")
        record(report, "delayed status text appears and wait succeeds", {
            "elapsed_seconds": elapsed, "result": wait_data,
        })

        timeout_result, timeout_data = cli(
            report, "valid_nonmatch_wait_timeout", "wait", "--window-id", window_id,
            "--text", "Valid Missing Fixture State 837", "--source", "ax",
            "--timeout", "1.25", "--interval", "0.25", timeout=10,
        )
        ensure_operation(timeout_result, timeout_data, code=1, status="fail",
                         label="valid nonmatching text wait")
        if timeout_data.get("reason") != "timeout":
            raise AssertionError(f"valid nonmatch should fail by timeout: {timeout_data}")
        record(report, "valid nonmatching text wait times out", {"result": timeout_data})

        try:
            ocr_result, ocr_data = cli(
                report, "ocr_inspect", "inspect", "--window-id", window_id,
                "--source", "ocr", "--timeout", "15", timeout=30,
            )
            ensure_operation(ocr_result, ocr_data, code=0, status="pass", label="OCR inspect")
            ocr_records = list(walk_records(ocr_data))
            canvas = find_record(ocr_records, "Canvas Evidence")
            confidence = canvas.get("confidence", canvas.get("score"))
            if not isinstance(confidence, (float, int)) or confidence < 0.8:
                raise AssertionError(f"canvas OCR confidence must be at least 0.8: {canvas}")
            record(report, "OCR finds custom-drawn canvas text with confidence", {
                "element": canvas, "confidence_threshold": 0.8,
            })

            assert_result(report, "OCR text present passes", window_id, "ocr",
                          ["--text", "Canvas Evidence", "--match", "exact", "--min-confidence", "0.8"],
                          expected_code=0)
            _, absent_data = assert_result(
                report, "OCR absent unknown text is insufficient evidence", window_id, "ocr",
                ["--text-absent", "No Such Canvas Text 837", "--match", "exact",
                 "--min-confidence", "0.8"], expected_code=2, status="error",
            )
            if error_code(absent_data) != "insufficient_evidence":
                raise AssertionError(f"OCR absence must be insufficient evidence: {absent_data}")
            assert_result(report, "OCR absence fails for recognized high confidence text", window_id, "ocr",
                          ["--text-absent", "Canvas Evidence", "--match", "exact",
                           "--min-confidence", "0.8"], expected_code=1, status="fail")
        except PermissionUnavailable as exc:
            report["limits"].append(f"OCR checks were blocked by OS permission: {exc}")

        if "OCR text present passes" in report["scenarios"]:
            # OCR region bounds remain in window-local points after cropping.
            # Derive this crop from the recognized text rectangle so the test
            # checks the crop-to-window coordinate mapping on real evidence.
            canvas_x, canvas_y, canvas_width, canvas_height = rectangle(canvas.get("bounds"))
            _, _, window_width, window_height = rectangle(window["bounds"])
            crop_x = max(0.0, canvas_x - 8.0)
            crop_y = max(0.0, canvas_y - 8.0)
            crop_right = min(window_width, canvas_x + canvas_width + 8.0)
            crop_bottom = min(window_height, canvas_y + canvas_height + 8.0)
            canvas_region = (
                f"{crop_x:.2f},{crop_y:.2f},"
                f"{crop_right - crop_x:.2f},{crop_bottom - crop_y:.2f}"
            )
            crop_result, crop_data = cli(
                report, "ocr_text_region_crop", "inspect", "--window-id", window_id,
                "--source", "ocr", "--region", canvas_region, "--timeout", "15", timeout=30,
            )
            ensure_operation(crop_result, crop_data, code=0, status="pass", label="OCR text region")
            crop_canvas = find_record(list(walk_records(crop_data)), "Canvas Evidence")
            crop_confidence = crop_canvas.get("confidence")
            if not isinstance(crop_confidence, (float, int)) or crop_confidence < 0.8:
                raise AssertionError(f"cropped canvas OCR confidence must be at least 0.8: {crop_canvas}")
            crop_artifact = crop_data.get("artifacts", {}).get("crop")
            if not crop_artifact or not Path(crop_artifact).is_file():
                raise AssertionError(f"OCR region did not publish its crop image: {crop_data}")
            cx, cy, cw, ch = rectangle(crop_canvas.get("bounds"))
            if not (crop_x <= cx + cw / 2 < crop_right and crop_y <= cy + ch / 2 < crop_bottom):
                raise AssertionError(f"cropped OCR bounds were not mapped to window points: {crop_canvas}")
            record(report, "OCR region crop preserves window-local point bounds", {
                "region": canvas_region,
                "crop_artifact": crop_artifact,
                "element": crop_canvas,
                "confidence_threshold": 0.8,
            })

            # An empty crop has no OCR observations. A nonempty absence query
            # over it must return insufficient evidence instead of a false pass.
            blank_x = min(max(0.0, canvas_x + canvas_width + 12.0), window_width - 36.0)
            blank_y = min(max(0.0, canvas_y + canvas_height / 2 - 10.0), window_height - 24.0)
            blank_region = f"{blank_x:.2f},{blank_y:.2f},32,20"
            blank_result, blank_data = cli(
                report, "ocr_blank_region_absence", "assert", "--window-id", window_id,
                "--source", "ocr", "--region", blank_region,
                "--text-absent", "No Text In Blank Crop 837", "--match", "exact",
                "--min-confidence", "0.8", timeout=30,
            )
            ensure_operation(blank_result, blank_data, code=2, status="error",
                             label="blank OCR region absence")
            if error_code(blank_data) != "insufficient_evidence":
                raise AssertionError(f"blank OCR crop must be insufficient evidence: {blank_data}")
            record(report, "blank OCR region absence returns insufficient evidence", {
                "region": blank_region, "result": blank_data,
            })

            # This valid exact query does not match the complete recognized
            # phrase. OCR still cannot use that observation to prove absence.
            partial_result, partial_data = cli(
                report, "ocr_partial_query", "assert", "--window-id", window_id,
                "--source", "ocr", "--text-absent", "Canvas", "--match", "exact",
                "--min-confidence", "0.8", timeout=30,
            )
            ensure_operation(partial_result, partial_data, code=2, status="error",
                             label="exact nonmatching OCR absence query")
            if error_code(partial_data) != "insufficient_evidence":
                raise AssertionError(f"OCR absence should be insufficient evidence: {partial_data}")
            record(report, "exact nonmatching OCR absence query returns insufficient evidence", {
                "result": partial_data,
            })

        command("v07-close")
        stale_result, stale_data = cli(
            report, "stale_closed_window", "inspect", "--window-id", window_id,
            "--source", "ax", "--timeout", "15", timeout=30,
        )
        ensure_operation(stale_result, stale_data, code=2, status="error",
                         label="closed stale window")
        record(report, "closed window ID returns an operational error", {"result": stale_data})

        if report["limits"]:
            report["status"] = "partial"
        else:
            report["status"] = "pass"
    except PermissionUnavailable as exc:
        report["limits"].append(str(exc))
        report["status"] = "partial"
        print(f"PARTIAL {exc}", file=sys.stderr)
    except (
        AssertionError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
        TypeError,
    ) as exc:
        report["error"] = str(exc) or type(exc).__name__
        print(f"FAIL {report['error']}", file=sys.stderr)
    finally:
        if fixture is not None and fixture.poll() is None:
            try:
                fixture.communicate("quit\n", timeout=5)
            except subprocess.TimeoutExpired:
                fixture.terminate()
                try:
                    fixture.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    fixture.kill()
                    fixture.wait(timeout=5)
        report_path = output / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(report_path)
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
