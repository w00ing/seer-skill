#!/usr/bin/env python3
"""Opt-in macOS QA for Seer v0.6 stable-window capture and evidence replay.

Build and run from the repository root:

    mkdir -p .seer/qa
    swiftc tests/manual/window_fixture.swift -o .seer/qa/SeerWindowFixture
    python3 tests/manual/verify_v06.py

The script launches and controls only SeerWindowFixture, captures only its
exact window ID, and stores reports, screenshots, and fixture-only baselines in
an owned .seer/qa/v06-* directory. It does not inspect other apps or change
permissions. It is intentionally not part of CI.
"""

import hashlib
import json
import os
import platform
import select
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, __version__ as pillow_version


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "skills/seer/scripts/seer"
FIXTURE = ROOT / ".seer/qa/SeerWindowFixture"
QA = ROOT / ".seer/qa"
COMPARE_IMAGES = ROOT / "skills/seer/scripts/compare_images.py"


def cli(*args, timeout=30):
    return subprocess.run(
        [sys.executable, str(CLI), *map(str, args), "--json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=ROOT,
    )


def payload(result):
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"Seer returned invalid JSON: {result.stdout!r}; {result.stderr.strip()}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Seer returned a non-object JSON payload")
    return value


def snapshot():
    result = cli("windows")
    if result.returncode:
        raise RuntimeError(f"window discovery failed: {result.stderr.strip() or result.stdout.strip()}")
    return {
        window["title"]: window
        for window in payload(result).get("windows", [])
        if window.get("process") == "SeerWindowFixture"
    }


def wait_window(title, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows = snapshot()
        if title in windows:
            return windows[title]
        time.sleep(0.2)
    raise RuntimeError(f"fixture window {title!r} did not appear")


def capture(window_id, path):
    result = cli("capture", "--window-id", window_id, "--out", path)
    data = payload(result)
    if result.returncode or data.get("status") != "pass":
        raise RuntimeError(f"fixture capture failed: {result.stderr.strip() or data}")
    if data.get("window_id") != window_id:
        raise AssertionError(f"capture returned the wrong window ID: {data.get('window_id')!r}")
    if Path(data.get("artifacts", {}).get("current", "")).resolve() != Path(path).resolve():
        raise AssertionError("capture did not write the requested path")
    if not Path(path).is_file():
        raise AssertionError(f"capture did not create {path}")
    metadata_path = data.get("artifacts", {}).get("metadata")
    if not metadata_path or not Path(metadata_path).is_file():
        raise AssertionError("capture metadata sidecar is missing")
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    assert metadata.get("captured_at"), metadata
    assert metadata.get("window_id") == window_id, metadata
    assert data.get("capture", {}).get("captured_at"), data
    assert data["capture"].get("window_id") == window_id, data
    return data


def wait_stable(window_id, out, timeout, ignore_rect=None):
    args = [
        "wait", "--stable", "--window-id", window_id,
        "--timeout", timeout, "--interval", "0.2", "--stable-for", "0.5",
        "--max-diff-percent", "0",
    ]
    if ignore_rect is not None:
        args.extend(["--ignore-rect", ignore_rect])
    args.extend(["--out", out])
    result = cli(*args, timeout=float(timeout) + 20)
    return result, payload(result)


def changed_bbox(before, after):
    with Image.open(before) as first, Image.open(after) as second:
        first.load()
        second.load()
        if first.size != second.size:
            raise AssertionError(f"fixture capture size changed: {first.size} != {second.size}")
        diff = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
        bbox = diff.getbbox()
        return bbox, first.size


def derive_mask(baseline, changed):
    bbox, size = changed_bbox(baseline, changed)
    if bbox is None:
        raise AssertionError("fixture animation produced no changed pixels")
    x0, y0, x1, y1 = bbox
    # Leave a small pixel margin around antialiased view edges. Coordinates are
    # derived from the captured PNG itself, so scale and titlebar framing are
    # already included without a point-to-pixel guess.
    pad = 4
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(size[0], x1 + pad)
    y1 = min(size[1], y1 + pad)
    if (x1 - x0) * (y1 - y0) > size[0] * size[1] * 0.2:
        raise AssertionError(f"dynamic region is implausibly large for a local mask: {bbox}, {size}")
    return f"{x0},{y0},{x1 - x0},{y1 - y0}", {
        "rectangle": [x0, y0, x1 - x0, y1 - y0],
        "png_size": {"width": size[0], "height": size[1]},
        "raw_changed_bbox": list(bbox),
        "source": "captured PNG pixel diff; titlebar offset and backing scale are included",
    }


def assert_wait_success(result, data, out, window_id):
    assert result.returncode == 0, (result.returncode, data, result.stderr)
    assert data.get("status") == "pass" and data.get("reason") == "stable", data
    assert data.get("window_id") == window_id, data
    assert Path(out).is_file(), f"stable image is missing: {out}"
    sidecar = Path(f"{out}.seer.json")
    assert sidecar.is_file(), f"stable capture metadata is missing: {sidecar}"
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    assert metadata.get("captured_at"), metadata
    assert metadata.get("window_id") == window_id, metadata
    capture_metadata = data.get("capture")
    assert isinstance(capture_metadata, dict), data
    assert capture_metadata.get("captured_at"), capture_metadata
    assert capture_metadata.get("window_id") == window_id, capture_metadata


def replay_bundle(verify_report, expected_ignore_rect):
    artifacts = verify_report.get("artifacts", {})
    bundle = Path(artifacts.get("bundle", ""))
    manifest_path = Path(artifacts.get("manifest", ""))
    if not bundle.is_dir() or not manifest_path.is_file():
        raise AssertionError(f"verify did not publish its bundle and manifest: {artifacts}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    options = manifest.get("comparison_options")
    assert isinstance(options, dict), manifest
    assert options.get("resize") is False, options
    assert len(options.get("ignore_rects", [])) == 1, options
    assert options["ignore_rects"][0] == expected_ignore_rect, options
    assert options.get("max_diff_percent") == 0, options

    # Move both source images away after bundling. Replay must resolve only the
    # immutable baseline/current copies in the evidence bundle.
    source_paths = [
        Path(manifest["images"][name]["source_path"])
        for name in ("baseline", "current")
    ]
    moved = []
    for source in source_paths:
        if source.exists():
            destination = source.with_name(source.name + ".moved-after-bundle")
            if destination.exists():
                destination.unlink()
            os.replace(source, destination)
            moved.append((source, destination))
        source_sidecar = Path(f"{source}.seer.json")
        if source_sidecar.exists():
            destination_sidecar = source_sidecar.with_name(source_sidecar.name + ".moved-after-bundle")
            if destination_sidecar.exists():
                destination_sidecar.unlink()
            os.replace(source_sidecar, destination_sidecar)
            moved.append((source_sidecar, destination_sidecar))
    assert all(not source.exists() for source in source_paths), source_paths

    replay = manifest.get("replay", {})
    command = list(replay.get("command", []))
    if len(command) < 3:
        raise AssertionError(f"manifest replay command is malformed: {replay}")
    compare_placeholder = "<seer-scripts>/compare_images.py"
    if command[1] == compare_placeholder:
        command[1] = str(COMPARE_IMAGES)
    elif Path(command[1]).name == "compare_images.py":
        command[1] = str(COMPARE_IMAGES)
    working_directory = replay.get("working_directory", ".")
    replay_cwd = (bundle / working_directory).resolve()
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, cwd=replay_cwd)
    try:
        replayed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bundle replay returned invalid JSON: {result.stdout!r}; {result.stderr.strip()}") from exc
    assert result.returncode == 0, (result.returncode, replayed, result.stderr)
    assert replayed.get("status") == verify_report.get("status") == manifest.get("status") == "pass", replayed
    assert replayed.get("options", {}).get("max_diff_percent") == options["max_diff_percent"], replayed
    expected_rect = [int(value) for value in expected_ignore_rect.split(",")]
    assert replayed.get("options", {}).get("ignore_rects") == [expected_rect], replayed
    for metric in ("pixels_changed", "pixels_total", "percent_changed", "avg_diff_percent", "size", "resized"):
        if metric in verify_report and metric in replayed:
            assert replayed[metric] == verify_report[metric], (metric, replayed[metric], verify_report[metric])
    return {
        "bundle": str(bundle),
        "manifest": str(manifest_path),
        "manifest_options": options,
        "replay_command": command,
        "replay_status": replayed["status"],
        "replayed_metrics": {key: replayed.get(key) for key in
                              ("pixels_changed", "pixels_total", "percent_changed", "avg_diff_percent")},
        "moved_original_paths": [[str(old), str(new)] for old, new in moved],
    }


def main():
    QA.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="v06-", dir=QA))
    swift_version = subprocess.run(["swiftc", "--version"], capture_output=True,
                                   text=True, timeout=10, cwd=ROOT)
    report = {
        "date": datetime.now().astimezone().isoformat(),
        "machine_versions": {
            "platform": platform.platform(),
            "macos": platform.mac_ver()[0],
            "python": platform.python_version(),
            "pillow": pillow_version,
            "swiftc": swift_version.stdout.strip() if swift_version.returncode == 0 else None,
            "seer_cli_sha256": hashlib.sha256(CLI.read_bytes()).hexdigest(),
        },
        "output_directory": str(output),
        "fixture_commands": ["v06-start", "v06-static", "v06-settle", "v06-pulse", "v06-outside", "quit"],
        "scenarios": {},
        "status": "error",
    }
    fixture = None

    def acknowledge(expected):
        ready, _, _ = select.select([fixture.stdout], [], [], 10)
        if not ready:
            raise RuntimeError(f"fixture did not acknowledge {expected}")
        actual = fixture.stdout.readline().strip()
        if actual != expected:
            raise RuntimeError(f"fixture acknowledgement mismatch: expected {expected!r}, got {actual!r}")

    def command(value):
        fixture.stdin.write(value + "\n")
        fixture.stdin.flush()
        acknowledge(value)

    def record(name, details):
        report["scenarios"][name] = details
        print(f"PASS {name}", flush=True)

    try:
        if not FIXTURE.is_file():
            raise RuntimeError(f"fixture executable is missing; build it first: {FIXTURE}")
        fixture = subprocess.Popen(
            [str(FIXTURE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=ROOT,
        )
        acknowledge("ready")
        command("v06-start")
        window = wait_window("Seer QA v0.6")
        window_id = window["window_id"]
        report["fixture_window"] = window

        command("v06-static")
        static_out = output / "static.png"
        static_result, static_data = wait_stable(window_id, static_out, 8)
        assert_wait_success(static_result, static_data, static_out, window_id)
        record("static window reaches stable and publishes capture metadata", {
            "returncode": static_result.returncode, "result": static_data,
        })

        command("v06-settle")
        settle_out = output / "settled.png"
        settle_result, settle_data = wait_stable(window_id, settle_out, 10)
        assert_wait_success(settle_result, settle_data, settle_out, window_id)
        record("changing window becomes stable after it settles", {
            "returncode": settle_result.returncode, "result": settle_data,
        })

        command("v06-static")
        mask_base = output / "mask-base.png"
        capture(window_id, mask_base)
        command("v06-pulse")
        time.sleep(0.22)
        mask_current = output / "mask-current.png"
        capture(window_id, mask_current)
        ignore_rect, mask_evidence = derive_mask(mask_base, mask_current)
        report["ignore_rect"] = mask_evidence

        # Seed an existing destination and sidecar so timeout preservation is
        # checked byte-for-byte while the fixture keeps changing.
        timeout_out = output / "timeout-preserved.png"
        timeout_sidecar = Path(f"{timeout_out}.seer.json")
        shutil.copyfile(static_out, timeout_out)
        shutil.copyfile(Path(f"{static_out}.seer.json"), timeout_sidecar)
        before_image = timeout_out.read_bytes()
        before_metadata = timeout_sidecar.read_bytes()
        timeout_result, timeout_data = wait_stable(window_id, timeout_out, 6)
        assert timeout_result.returncode == 1, (timeout_result.returncode, timeout_data)
        assert timeout_data.get("status") == "fail" and timeout_data.get("reason") == "timeout", timeout_data
        assert timeout_data["samples"] >= 2, "must observe changing frames before timeout"
        assert timeout_data["last_comparison"]["pixels_changed"] > 0, timeout_data
        assert timeout_out.read_bytes() == before_image, "timed-out wait replaced the existing image"
        assert timeout_sidecar.read_bytes() == before_metadata, "timed-out wait replaced existing metadata"
        record("continuous change times out and preserves existing image and metadata", {
            "returncode": timeout_result.returncode, "result": timeout_data,
            "preserved_sha256": hashlib.sha256(before_image).hexdigest(),
        })

        masked_out = output / "masked-stable.png"
        masked_result, masked_data = wait_stable(window_id, masked_out, 8, ignore_rect)
        assert_wait_success(masked_result, masked_data, masked_out, window_id)
        record("continuous change inside the derived mask is ignored", {
            "returncode": masked_result.returncode, "ignore_rect": ignore_rect,
            "result": masked_data,
        })

        command("v06-outside")
        outside_out = output / "outside-fail.png"
        outside_result, outside_data = wait_stable(window_id, outside_out, 6, ignore_rect)
        assert outside_result.returncode == 1, (outside_result.returncode, outside_data)
        assert outside_data.get("status") == "fail", outside_data
        assert outside_data["samples"] >= 2, "must observe outside-mask changes before timeout"
        assert outside_data["last_comparison"]["pixels_changed"] > 0, outside_data
        assert not outside_out.exists(), "failed wait published an output image"
        record("continuous change outside the mask fails", {
            "returncode": outside_result.returncode, "ignore_rect": ignore_rect,
            "result": outside_data,
        })

        # Create a disposable baseline from the fixture's static capture, then
        # verify a pulse capture with the pixel-derived ignore mask.
        verify_loop = output / "fixture-only-baselines"
        create = cli("verify", mask_base, "v06-fixture-only", "--loop-dir", verify_loop,
                     "--create-baseline", "--max-diff-percent", "0")
        create_data = payload(create)
        assert create.returncode == 0 and create_data.get("status") == "pass", create_data
        command("v06-pulse")
        time.sleep(0.22)
        bundle_current = output / "bundle-current.png"
        capture(window_id, bundle_current)
        verify = cli("verify", bundle_current, "v06-fixture-only", "--loop-dir", verify_loop,
                     "--max-diff-percent", "0", "--ignore-rect", ignore_rect)
        verify_data = payload(verify)
        assert verify.returncode == 0 and verify_data.get("status") == "pass", verify_data
        bundle_evidence = replay_bundle(verify_data, ignore_rect)
        record("verify evidence replays from its manifest after source paths move", {
            "returncode": verify.returncode, "verify_result": verify_data,
            "replay": bundle_evidence,
        })

        report["status"] = "pass"
    except (AssertionError, OSError, RuntimeError, ValueError, KeyError,
            subprocess.TimeoutExpired, TypeError) as exc:
        report["error"] = str(exc) or type(exc).__name__
        print(f"FAIL {report['error']}", file=sys.stderr)
        if fixture is not None and fixture.poll() is not None:
            report["fixture_stderr"] = fixture.stderr.read()
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
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(output / "report.json")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
