"""Bounded, exact-window stability checks using the same capture and diff paths."""
import json
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from capture_metadata import publish_image


class WaitError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def capture_sample(cli, window_id, output, timeout):
    """Kill the complete owned capture process group when its budget expires."""
    command = [sys.executable, str(cli), "capture", "--window-id", str(window_id),
               "--out", str(output), "--json"]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as child:
        try:
            stdout, stderr = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The deadline can race with the child's normal exit.
            child.communicate()
            raise TimeoutError("capture exhausted the wait timeout")
    try:
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise WaitError("invalid_subprocess_output", "capture returned invalid JSON during wait") from exc
    if child.returncode:
        error = payload.get("error", {})
        if (not isinstance(error, dict) or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)):
            raise WaitError("invalid_subprocess_output", "capture returned an invalid error object")
        raise WaitError(error.get("code", "subprocess_failed"),
                        error.get("message", stderr.strip() or "capture failed during wait"))
    if (payload.get("schema_version") != 1 or payload.get("operation") != "capture"
            or payload.get("status") != "pass" or payload.get("window_id") != window_id):
        raise WaitError("invalid_subprocess_output", "capture returned inconsistent window evidence")
    metadata = payload.get("capture")
    if (not isinstance(metadata, dict) or metadata.get("source") != "seer.capture"
            or metadata.get("schema_version") != 1 or metadata.get("window_id") != window_id
            or not isinstance(metadata.get("captured_at"), str) or not metadata["captured_at"]
            or not output.is_file()
            or metadata.get("image_sha256") != hashlib.sha256(output.read_bytes()).hexdigest()):
        raise WaitError("invalid_subprocess_output", "capture metadata does not match the sampled image")
    return payload


def wait_stable(args, cli):
    from compare_images import compare_images, parse_rect

    rectangles = [parse_rect(value) for value in args.ignore_rect]
    started = time.monotonic()
    deadline = started + args.timeout
    samples = 0
    anchor = None
    stable_since = None
    last_comparison = None
    output_root = Path(os.environ.get("SEER_OUT_DIR", os.environ.get("SEER_TMP_DIR", ".seer")))
    output = args.out or str(output_root / "capture" / f"stable-{args.window_id}-{uuid.uuid4().hex}.png")
    condition = {"stable_for": args.stable_for, "interval": args.interval,
                 "timeout": args.timeout, "max_diff_percent": args.max_diff_percent,
                 "ignore_rects": [list(rect) for rect in rectangles], "reference": "interval_anchor"}

    def result(status, reason, artifacts=None, metadata=None):
        if last_comparison:
            last_comparison.pop("baseline", None)
            last_comparison.pop("current", None)
        return {"schema_version": 1, "operation": "wait", "status": status,
                "reason": reason, "window_id": args.window_id, "samples": samples,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "condition": condition, "last_comparison": last_comparison,
                "artifacts": artifacts or {}, "capture": metadata}

    staging_parent = Path(output).expanduser().resolve().parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    # Captures stage below their destination. The parent wait owns this whole
    # directory and can remove it even after killing a timed-out capture.
    with tempfile.TemporaryDirectory(prefix=".seer-wait-", dir=staging_parent) as directory:
        temporary = Path(directory)
        while time.monotonic() < deadline:
            frame = temporary / f"frame-{samples % 2}.png"
            try:
                payload = capture_sample(cli, args.window_id, frame, deadline - time.monotonic())
            except TimeoutError:
                return result("fail", "timeout"), 1
            samples += 1
            sampled = time.monotonic()
            # Validate masks even for the first sample; an invalid condition is
            # an error, never a successful wait or an ordinary timeout.
            last_comparison = compare_images(anchor or frame, frame, ignore_rects=rectangles,
                                             max_diff_percent=args.max_diff_percent)
            if anchor is None or last_comparison["status"] == "fail":
                anchor = temporary / "anchor.png"
                anchor.write_bytes(frame.read_bytes())
                stable_since = sampled
            elif sampled - stable_since >= args.stable_for and time.monotonic() < deadline:
                metadata = payload["capture"]
                artifacts = publish_image(frame, output, metadata)
                # Temporary comparison paths are not usable artifacts after return.
                last_comparison.pop("baseline", None)
                last_comparison.pop("current", None)
                return result("pass", "stable", artifacts, metadata), 0
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(args.interval, remaining))
    if last_comparison:
        last_comparison.pop("baseline", None)
        last_comparison.pop("current", None)
    return result("fail", "timeout"), 1
