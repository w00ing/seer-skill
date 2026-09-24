#!/usr/bin/env python3
"""Opt-in macOS desktop QA. Run from the repo root after building the fixture.

    mkdir -p .seer/qa
    swiftc tests/manual/window_fixture.swift -o .seer/qa/SeerWindowFixture
    python3 tests/manual/verify_window_ids.py

Creates and controls only the fixture's windows. Screenshots stay in .seer/qa/;
no baseline is created, and no existing user app is modified.
"""
import hashlib
import json
import platform
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime

from PIL import Image, __version__ as pillow_version


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "skills/seer/scripts/seer"
QA = ROOT / ".seer/qa"


def cli(*args):
    return subprocess.run([sys.executable, str(CLI), *map(str, args), "--json"],
                          capture_output=True, text=True, timeout=20, cwd=ROOT)


def snapshot():
    result = cli("windows")
    if result.returncode:
        raise RuntimeError(result.stderr)
    return {window["title"]: window for window in json.loads(result.stdout)["windows"]
            if window["process"] == "SeerWindowFixture"}


def wait_windows(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        windows = snapshot()
        if predicate(windows):
            return windows
        time.sleep(0.2)
    raise RuntimeError("fixture windows did not reach the expected state")


def main():
    QA.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="window-ids-", dir=QA))
    report = {"date": datetime.now().astimezone().isoformat(), "macos": platform.mac_ver()[0],
              "python": platform.python_version(), "pillow": pillow_version, "checks": [], "status": "error"}
    fixture = None

    def acknowledge(expected):
        ready, _, _ = select.select([fixture.stdout], [], [], 10)
        if not ready or fixture.stdout.readline().strip() != expected:
            raise RuntimeError(f"fixture did not acknowledge {expected}")

    def command(value):
        fixture.stdin.write(value + "\n")
        fixture.stdin.flush()
        acknowledge(value)

    def record(name):
        report["checks"].append(name)
        print(f"PASS {name}", flush=True)

    def capture(window_id, name, color):
        path = output / f"{name}.png"
        result = cli("capture", "--window-id", window_id, "--out", path)
        if result.returncode:
            raise RuntimeError(result.stderr)
        payload = json.loads(result.stdout)
        assert payload["window_id"] == window_id
        assert Path(payload["artifacts"]["current"]) == path
        with Image.open(path) as image:
            red, green, blue = image.convert("RGB").getpixel((10, image.height - 10))
        assert (red > 2 * max(green, blue)) if color == "red" else (blue > 2 * max(red, green))
        return path

    try:
        fixture = subprocess.Popen([str(QA / "SeerWindowFixture")], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True, cwd=ROOT)
        acknowledge("ready")
        windows = wait_windows(lambda w: "Seer QA A" in w and "Seer QA B" in w)
        a, b = windows["Seer QA A"], windows["Seer QA B"]
        report["initial_windows"] = windows
        doctor = cli("doctor")
        assert doctor.returncode == 0, doctor.stderr
        doctor_payload = json.loads(doctor.stdout)
        assert doctor_payload["status"] == "pass"
        assert doctor_payload["capabilities"]["window_query"]["authorized"]
        if a["frontmost"]:
            assert doctor_payload["frontmost_process"] == "SeerWindowFixture"
        record("doctor returns a successful capability report")
        current_a = capture(a["window_id"], "initial-A", "red")
        capture(b["window_id"], "initial-B", "blue")
        record("two windows capture their own content")

        loop = output / "missing-baseline-loop"
        missing = cli("verify", current_a, "unapproved", "--loop-dir", loop)
        assert missing.returncode == 3, missing.stderr
        assert json.loads(missing.stdout)["status"] == "needs_baseline"
        assert not loop.exists()
        record("missing baseline returns needs_baseline without creating artifacts")

        command("move")
        moved = wait_windows(lambda w: w.get("Seer QA A", {}).get("bounds") != a["bounds"] and "Seer QA A" in w)
        assert moved["Seer QA A"]["window_id"] == a["window_id"]
        capture(a["window_id"], "moved-A", "red")
        record("moving preserves window ID and capture target")

        command("reorder")
        reordered = wait_windows(lambda w: w.get("Seer QA A", {}).get("index") != a["index"] and "Seer QA A" in w)
        assert reordered["Seer QA A"]["window_id"] == a["window_id"]
        capture(a["window_id"], "reordered-A", "red")
        capture(b["window_id"], "reordered-B", "blue")
        record("reordering preserves both capture targets")

        command("close")
        wait_windows(lambda w: "Seer QA A" not in w)
        before = current_a.read_bytes()
        report["preserved_capture_sha256"] = hashlib.sha256(before).hexdigest()
        stale = cli("capture", "--window-id", a["window_id"], "--out", current_a)
        assert stale.returncode == 2, stale.stdout
        assert current_a.read_bytes() == before
        report["stale_error"] = json.loads(stale.stdout)
        assert report["stale_error"]["status"] == "error"
        record("closed ID fails and preserves previous capture")

        command("recreate")
        recreated = wait_windows(lambda w: "Seer QA A" in w)
        new_a = recreated["Seer QA A"]["window_id"]
        assert new_a != a["window_id"]
        capture(new_a, "recreated-A", "red")
        stale_again = cli("capture", "--window-id", a["window_id"], "--out", current_a)
        assert stale_again.returncode == 2, stale_again.stdout
        assert json.loads(stale_again.stdout)["status"] == "error"
        assert current_a.read_bytes() == before
        report["recreated_window_id"] = new_a
        record("recreated window is discovered with a new ID")
        report["status"] = "pass"
    except (AssertionError, OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc) or type(exc).__name__
        print(f"FAIL {report['error']}", file=sys.stderr)
    finally:
        if fixture is not None and fixture.poll() is None:
            try:
                fixture.communicate("quit\n", timeout=5)
            except subprocess.TimeoutExpired:
                fixture.terminate()
                fixture.wait(timeout=5)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(output / "report.json")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
