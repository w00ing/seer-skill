import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "seer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from compare_images import ComparisonError, compare_images, parse_rect  # noqa: E402


class ImageComparisonTests(unittest.TestCase):
    def assert_comparison_error(self, code, baseline, current, **kwargs):
        with self.assertRaises(ComparisonError) as raised:
            compare_images(baseline, current, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertTrue(raised.exception.message)
        self.assertIsInstance(raised.exception.details, dict)
        return raised.exception

    def test_overlapping_masks_are_counted_once_and_removed_from_diff_image(self):
        baseline = Image.new("RGB", (4, 2), "black")
        current = Image.new("RGB", (4, 2), "white")
        with tempfile.TemporaryDirectory() as tmp:
            diff = Path(tmp) / "diff.png"
            result = compare_images(
                baseline,
                current,
                ignore_rects=[(0, 0, 2, 2), (1, 0, 2, 2)],
                diff_out=diff,
            )
            self.assertEqual(result["pixels_image_total"], 8)
            self.assertEqual(result["pixels_excluded"], 6)
            self.assertEqual(result["pixels_total"], 2)
            self.assertEqual(result["pixels_changed"], 2)
            self.assertEqual(result["percent_changed"], 100.0)

            with Image.open(diff) as rendered:
                self.assertEqual(rendered.mode, "RGBA")
                self.assertEqual(rendered.getpixel((0, 0))[3], 0)
                self.assertEqual(rendered.getpixel((3, 0))[3], 255)

    def test_alpha_difference_counts_and_threshold_uses_unrounded_percentage(self):
        baseline = Image.new("RGBA", (2, 1), (0, 0, 0, 255))
        current = baseline.copy()
        current.putpixel((0, 0), (0, 0, 0, 254))

        failed = compare_images(baseline, current, max_diff_percent=49.999)
        passed = compare_images(baseline, current, max_diff_percent=50.0)
        self.assertEqual(failed["pixels_changed"], 1)
        self.assertEqual(failed["pixels_total"], 2)
        self.assertEqual(failed["percent_changed"], 50.0)
        self.assertEqual(failed["status"], "fail")
        self.assertEqual(passed["status"], "pass")

    def test_rectangle_parser_and_validation_reject_bad_regions(self):
        self.assertEqual(parse_rect("1, 2, 3, 4"), (1, 2, 3, 4))
        for value in ("1,2,3", "-1,0,1,1", "0,0,1.5,1", "0,0,0,1"):
            with self.subTest(value=value):
                with self.assertRaises(ComparisonError) as raised:
                    parse_rect(value)
                self.assertEqual(raised.exception.code, "invalid_arguments")

        baseline = Image.new("RGB", (3, 2), "black")
        current = baseline.copy()
        outside = self.assert_comparison_error(
            "invalid_arguments", baseline, current, ignore_rects=[(2, 0, 2, 1)]
        )
        self.assertEqual(outside.details["rectangle"], [2, 0, 2, 1])
        self.assert_comparison_error(
            "invalid_arguments",
            baseline,
            current,
            ignore_rects=[(0, 0, 2, 2), (2, 0, 1, 2)],
        )

    def test_cli_mask_errors_have_structured_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            baseline = work / "baseline.png"
            current = work / "current.png"
            Image.new("RGB", (2, 2), "black").save(baseline)
            Image.new("RGB", (2, 2), "white").save(current)

            for rect in ("not-a-rect", "1,1,2,1", "0,0,2,2", None):
                with self.subTest(rect=rect):
                    command = [
                        sys.executable,
                        str(SCRIPTS / "compare_images.py"),
                        str(baseline),
                        str(current),
                        "--ignore-rect",
                    ]
                    if rect is not None:
                        command.append(rect)
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["schema_version"], 1)
                    self.assertEqual(payload["operation"], "verify")
                    self.assertEqual(payload["status"], "error")
                    self.assertEqual(payload["error"]["code"], "invalid_arguments")
                    self.assertTrue(payload["error"]["details"])
                    self.assertIn("error:", result.stderr)

    def test_size_mismatch_requires_explicit_resize_and_preserves_original_sizes(self):
        baseline = Image.new("RGB", (2, 2), "black")
        current = Image.new("RGB", (4, 4), "black")
        error = self.assert_comparison_error("image_size_mismatch", baseline, current)
        self.assertEqual(error.details["baseline_size"], {"width": 2, "height": 2})
        self.assertEqual(error.details["current_size"], {"width": 4, "height": 4})

        result = compare_images(baseline, current, resize=True)
        self.assertTrue(result["resized"])
        self.assertEqual(result["baseline_size"], {"width": 2, "height": 2})
        self.assertEqual(result["current_size"], {"width": 4, "height": 4})
        self.assertEqual(result["size"], {"width": 2, "height": 2})
        self.assertTrue(result["scale_evidence"]["normalization_applied"])

    def test_known_png_dpi_mismatch_requires_resize_and_unknown_dpi_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            baseline = work / "baseline.png"
            current = work / "current.png"
            Image.new("RGB", (2, 2), "black").save(baseline, dpi=(72, 72))
            Image.new("RGB", (2, 2), "black").save(current, dpi=(144, 144))

            error = self.assert_comparison_error("image_scale_mismatch", baseline, current)
            self.assertEqual(error.details["baseline_size"], {"width": 2, "height": 2})
            self.assertTrue(error.details["scale_evidence"]["dpi_mismatch"])

            normalized = compare_images(baseline, current, resize=True)
            self.assertFalse(normalized["resized"])
            self.assertTrue(normalized["scale_evidence"]["normalization_applied"])
            self.assertNotEqual(normalized["baseline_dpi"], normalized["current_dpi"])

            unknown = compare_images(
                Image.new("RGB", (2, 2), "black"),
                Image.new("RGB", (2, 2), "black"),
            )
            self.assertIsNone(unknown["baseline_dpi"])
            self.assertIsNone(unknown["current_dpi"])
            self.assertFalse(unknown["scale_evidence"]["png_dpi_comparable"])


if __name__ == "__main__":
    unittest.main()
