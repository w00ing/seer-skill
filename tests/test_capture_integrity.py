import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image


CAPTURE = Path(__file__).resolve().parents[1] / "skills/seer/scripts/capture_app_window.sh"


class CaptureIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.output = self.work / "output with spaces.png"
        self.source = self.work / "source.png"
        Image.new("RGB", (8, 8), "blue").save(self.source)
        Image.new("RGB", (8, 8), "red").save(self.output)
        self.original = self.output.read_bytes()
        self.env = os.environ | {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "SEER_TEST_SOURCE": str(self.source),
        }
        self.executable("osascript", "script=$(cat)\ncase \"$script\" in *position*) echo '0, 0';; *size*) echo '8, 8';; esac\n")

    def executable(self, name, source):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n" + source, encoding="utf-8")
        path.chmod(0o755)

    def capture(self, source, *, exact=True):
        self.executable("screencapture", 'for arg in "$@"; do output=$arg; done\n' + source)
        args = ["--window-id", "123", str(self.output)] if exact else [str(self.output), "Fixture"]
        return subprocess.run(["bash", str(CAPTURE), *args], cwd=self.work, env=self.env, text=True, capture_output=True)

    def assert_failure_preserves_output(self, result):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.output.read_bytes(), self.original)
        self.assertFalse(list(self.work.glob(".seer-capture.*")))

    def test_success_replaces_output_only_with_valid_png(self):
        for exact in (True, False):
            with self.subTest(exact=exact):
                self.output.write_bytes(self.original)
                result = self.capture('cp "$SEER_TEST_SOURCE" "$output"\n', exact=exact)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(self.output))
                self.assertEqual(self.output.read_bytes(), self.source.read_bytes())
                self.assertFalse(list(self.work.glob(".seer-capture.*")))

    def test_nonzero_capture_with_partial_output_preserves_destination(self):
        self.assert_failure_preserves_output(self.capture('echo partial > "$output"\nexit 1\n'))

    def test_closed_window_cannot_publish_cached_pixels(self):
        self.executable("osascript", "exit 1\n")
        self.assert_failure_preserves_output(self.capture('cp "$SEER_TEST_SOURCE" "$output"\n'))

    def test_window_closed_during_capture_preserves_destination(self):
        self.env["SEER_TEST_QUERY_COUNT"] = str(self.work / "queried")
        self.executable("osascript", 'if [[ -f "$SEER_TEST_QUERY_COUNT" ]]; then exit 1; fi\n: > "$SEER_TEST_QUERY_COUNT"\n')
        self.assert_failure_preserves_output(self.capture('cp "$SEER_TEST_SOURCE" "$output"\n'))

    def test_success_without_output_cannot_reuse_previous_image(self):
        self.assert_failure_preserves_output(self.capture("exit 0\n"))

    def test_empty_or_corrupt_capture_preserves_destination(self):
        for source in (': > "$output"\n', 'echo broken > "$output"\n'):
            with self.subTest(source=source):
                self.assert_failure_preserves_output(self.capture(source))

    def test_truncated_png_preserves_destination(self):
        self.source.write_bytes(self.source.read_bytes()[:-12])
        self.assert_failure_preserves_output(self.capture('cp "$SEER_TEST_SOURCE" "$output"\n'))

    def test_non_png_image_preserves_destination(self):
        Image.new("RGB", (8, 8), "blue").save(self.source, format="JPEG")
        self.assert_failure_preserves_output(self.capture('cp "$SEER_TEST_SOURCE" "$output"\n'))

    def test_failed_first_capture_leaves_no_output(self):
        self.output.unlink()
        result = self.capture("exit 0\n")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.work.glob(".seer-capture.*")))

    def test_publish_failure_preserves_destination_directory(self):
        self.output.unlink()
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        result = self.capture('cp "$SEER_TEST_SOURCE" "$output"\n')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertFalse(list(self.work.glob(".seer-capture.*")))


if __name__ == "__main__":
    unittest.main()
