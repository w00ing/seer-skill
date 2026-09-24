import argparse
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
import stable_wait
from capture_metadata import describe_capture
from compare_images import ComparisonError


class StableWaitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.clock = 0.0
        self.captures = 0
        self.output = self.work / "stable.png"
        self.args = argparse.Namespace(window_id=123, timeout=1.0, interval=0.1,
                                       stable_for=0.3, max_diff_percent=0,
                                       ignore_rect=[], out=str(self.output))

    def run_wait(self, render):
        def capture(cli, window_id, output, timeout):
            self.assertEqual(window_id, 123)
            self.assertGreater(timeout, 0)
            self.clock += 0.01
            render(self.captures).save(output)
            self.captures += 1
            return {"capture": describe_capture(output, window_id=window_id,
                                                process=None, started_at="fixture")}

        def sleep(seconds):
            self.clock += seconds

        with patch.object(stable_wait.time, "monotonic", side_effect=lambda: self.clock), \
                patch.object(stable_wait.time, "sleep", side_effect=sleep), \
                patch.object(stable_wait, "capture_sample", side_effect=capture):
            return stable_wait.wait_stable(self.args, SCRIPTS / "seer")

    def test_stable_screen_repeats_and_publishes_hash_bound_metadata(self):
        for _ in range(2):
            payload, code = self.run_wait(lambda n: Image.new("RGB", (10, 10), "white"))
            self.assertEqual(code, 0)
            self.assertEqual(payload["reason"], "stable")
            self.assertGreaterEqual(payload["samples"], 2)
            metadata = json.loads(Path(payload["artifacts"]["metadata"]).read_text())
            self.assertEqual(metadata["window_id"], 123)
            self.assertEqual(metadata["image_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_changing_screen_times_out_and_preserves_output(self):
        self.output.write_bytes(b"previous output")
        payload, code = self.run_wait(lambda n: Image.new("RGB", (10, 10), "white" if n % 2 else "black"))
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["reason"], "timeout")
        self.assertEqual(self.output.read_bytes(), b"previous output")
        self.assertFalse(payload["artifacts"])

    def test_gradual_drift_is_compared_to_interval_anchor(self):
        self.args.max_diff_percent = 10

        def frame(n):
            image = Image.new("RGB", (10, 1), "white")
            for x in range(min(n, 9)):
                image.putpixel((x, 0), (0, 0, 0))
            return image

        payload, code = self.run_wait(frame)
        self.assertEqual(code, 1, payload)
        self.assertFalse(self.output.exists())

    def test_mask_ignores_animation_but_not_outside_change(self):
        self.args.ignore_rect = ["0,0,1,1"]

        def frame(n, x=0):
            image = Image.new("RGB", (10, 10), "white")
            image.putpixel((x, 0), (n % 2 * 255,) * 3)
            return image

        self.assertEqual(self.run_wait(frame)[1], 0)
        self.output.unlink()
        self.assertEqual(self.run_wait(lambda n: frame(n, x=1))[1], 1)

    def test_all_excluded_is_error_before_success(self):
        self.args.ignore_rect = ["0,0,10,10"]
        with self.assertRaises(ComparisonError) as caught:
            self.run_wait(lambda n: Image.new("RGB", (10, 10), "white"))
        self.assertEqual(caught.exception.code, "invalid_arguments")
        self.assertFalse(self.output.exists())

    def test_capture_error_is_not_timeout(self):
        with patch.object(stable_wait, "capture_sample", side_effect=stable_wait.WaitError("subprocess_failed", "stale ID")):
            with self.assertRaises(stable_wait.WaitError):
                stable_wait.wait_stable(self.args, SCRIPTS / "seer")

    def test_subprocess_timeout_is_bounded(self):
        slow = self.work / "slow.py"
        marker = self.work / "descendant-survived"
        spawned = self.work / "spawned"
        descendant = "import time; from pathlib import Path; time.sleep(1); Path(" + repr(str(marker)) + ").touch()"
        slow.write_text("import subprocess, sys, time\nfrom pathlib import Path\n"
                        f"subprocess.Popen([sys.executable, '-c', {descendant!r}])\n"
                        f"Path({str(spawned)!r}).touch()\ntime.sleep(30)\n")
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            stable_wait.capture_sample(slow, 123, self.work / "out.png", 0.5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(spawned.exists(), "test must reach the descendant-spawn boundary")
        time.sleep(1.05)
        self.assertFalse(marker.exists(), "timeout must terminate descendants as well as the CLI")

    def test_malformed_capture_payload_is_structured_error(self):
        fake = self.work / "fake_capture.py"
        output = self.work / "frame.png"
        Image.new("RGB", (2, 2), "white").save(output)
        for payload, code in (({"error": None}, 2),
                              ({"schema_version": 1, "operation": "capture", "status": "pass",
                                "window_id": 123, "capture": None}, 0)):
            with self.subTest(payload=payload):
                fake.write_text(f"import sys\nprint({json.dumps(payload)!r})\nsys.exit({code})\n")
                with self.assertRaises(stable_wait.WaitError) as caught:
                    stable_wait.capture_sample(fake, 123, output, 2)
                self.assertEqual(caught.exception.code, "invalid_subprocess_output")

    def test_cli_timeout_cleans_owned_capture_staging(self):
        binaries = self.work / "bin"
        binaries.mkdir()
        bash = binaries / "bash"
        marker = self.work / "capture-started"
        bash.write_text(f"#!{sys.executable}\nimport sys, time\nfrom pathlib import Path\n"
                        "Path(sys.argv[-1]).write_bytes(b'partial capture')\n"
                        f"Path({str(marker)!r}).touch()\ntime.sleep(30)\n")
        bash.chmod(0o755)
        env = os.environ | {"PATH": str(binaries) + os.pathsep + os.environ['PATH']}
        result = subprocess.run([sys.executable, str(SCRIPTS / 'seer'), 'wait', '--stable',
                                 '--window-id', '123', '--timeout', '1.5', '--out', str(self.output)],
                                env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(marker.exists(), 'test must reach capture staging before timeout')
        self.assertEqual(json.loads(result.stdout)['reason'], 'timeout')
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.work.glob('.seer-*')), 'parent must clean killed capture staging')

    def test_cli_rejects_invalid_conditions_as_json(self):
        for extra in (["--timeout", "nan"], ["--stable-for", "0"],
                      ["--interval", "-1"], ["--max-diff-percent", "101"],
                      ["--ignore-rect", "bad"]):
            with self.subTest(extra=extra):
                result = subprocess.run([sys.executable, str(SCRIPTS / "seer"), "wait", "--stable",
                                         "--window-id", "123", *extra], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["error"]["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
