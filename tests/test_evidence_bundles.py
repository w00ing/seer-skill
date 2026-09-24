#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "seer" / "scripts"
LOOP_COMPARE = SCRIPTS / "loop_compare.sh"
COMPARE_IMAGES = SCRIPTS / "compare_images.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_capture_sidecar(image_path: Path, *, digest: str | None = None) -> Path:
    with Image.open(image_path) as image:
        size = {"width": image.width, "height": image.height}
    sidecar = Path(f"{image_path}.seer.json")
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "seer.capture",
                "image_sha256": digest or sha256(image_path),
                "captured_at": "2026-09-25T12:34:56.789Z",
                "window_id": 101,
                "process": "FakeApp",
                "size": size,
                "dpi": [144.0, 144.0],
            }
        ),
        encoding="utf-8",
    )
    return sidecar


class EvidenceBundleTests(unittest.TestCase):
    def run_loop(self, work: Path, loop_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ | {"SEER_LOOP_DIR": str(loop_dir)}
        return subprocess.run(
            ["bash", str(LOOP_COMPARE), *args],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
        )

    def run_cli(self, work: Path, loop_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ | {"SEER_LOOP_DIR": str(loop_dir)}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "seer"), "verify", *args, "--json"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_update_bundle_keeps_old_baseline_and_replays_after_sources_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            loop_dir = work / "loop"
            current = work / "current.png"
            original_baseline = Image.new("RGB", (4, 4), "white")
            baseline_path = loop_dir / "baselines" / "home.png"
            baseline_path.parent.mkdir(parents=True)
            original_baseline.save(baseline_path)
            changed = Image.new("RGB", (4, 4), "white")
            changed.putpixel((0, 0), (0, 0, 0))
            changed.save(current)

            result = self.run_loop(
                work,
                loop_dir,
                "--update-baseline",
                "--ignore-rect",
                "0,0,1,1",
                "--",
                str(current),
                "home",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(payload["baseline_updated"])
            bundle = Path(payload["artifacts"]["bundle"])
            baseline_snapshot = bundle / "baseline.png"
            current_snapshot = bundle / "current.png"
            self.assertEqual(payload["artifacts"]["baseline"], str(baseline_snapshot))
            self.assertEqual(payload["artifacts"]["current"], str(current_snapshot))
            self.assertEqual(Image.open(baseline_snapshot).getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(Image.open(current_snapshot).getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(baseline_path.read_bytes(), current.read_bytes())

            manifest_path = Path(payload["artifacts"]["manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["comparison_options"],
                {"resize": False, "ignore_rects": ["0,0,1,1"], "max_diff_percent": 0.0},
            )
            for role in ("baseline", "current", "report"):
                entry = manifest["files"][role]
                self.assertFalse(Path(entry["path"]).is_absolute())
                self.assertEqual(sha256(bundle / entry["path"]), entry["sha256"])

            # The replay uses only immutable bundle files and the recorded
            # options, so it still produces the same decision when both source
            # images have changed or disappeared.
            current.unlink()
            Image.new("RGB", (4, 4), "blue").save(baseline_path)
            replay = subprocess.run(
                manifest["replay"]["command"],
                cwd=bundle,
                capture_output=True,
                text=True,
            )
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(replay.stdout)["status"], payload["status"])
            self.assertEqual(json.loads(replay.stdout)["percent_changed"], payload["percent_changed"])

    def test_missing_baseline_has_no_side_effects_and_stale_sidecar_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            loop_dir = work / "loop"
            current = work / "current.png"
            Image.new("RGB", (4, 4), "white").save(current)
            write_capture_sidecar(current, digest="0" * 64)

            missing = self.run_loop(work, loop_dir, str(current), "home")
            self.assertEqual(missing.returncode, 3, missing.stderr)
            self.assertEqual(json.loads(missing.stdout)["status"], "needs_baseline")
            self.assertFalse(loop_dir.exists())

            created = self.run_loop(work, loop_dir, "--create-baseline", str(current), "home")
            self.assertEqual(created.returncode, 0, created.stderr)
            payload = json.loads(created.stdout)
            manifest = json.loads(Path(payload["artifacts"]["manifest"]).read_text(encoding="utf-8"))
            metadata = manifest["images"]["current"]["capture_metadata"]
            self.assertEqual(metadata["status"], "unknown")
            self.assertEqual(metadata["reason"], "image_sha256_mismatch")
            self.assertIsNone(metadata["captured_at"])
            self.assertIsNone(metadata["window_id"])
            self.assertFalse(Path(f"{payload['baseline_created']}.seer.json").exists())

    def test_invalid_and_fully_excluded_creation_do_not_approve_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            loop_dir = work / "loop"
            current = work / "current.png"
            current.write_bytes(b"not a PNG")

            invalid = self.run_loop(work, loop_dir, "--create-baseline", str(current), "home")
            self.assertEqual(invalid.returncode, 2)
            self.assertFalse(loop_dir.exists())

            Image.new("RGB", (4, 4), "white").save(current)
            excluded = self.run_loop(
                work,
                loop_dir,
                "--create-baseline",
                "--ignore-rect",
                "0,0,4,4",
                "--",
                str(current),
                "home",
            )
            self.assertEqual(excluded.returncode, 2)
            payload = json.loads(excluded.stdout)
            self.assertEqual(payload["error"]["code"], "invalid_arguments")
            self.assertFalse(loop_dir.exists())

    def test_dimension_error_saves_bundle_without_diff_or_baseline_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            loop_dir = work / "loop"
            baseline_path = loop_dir / "baselines" / "home.png"
            baseline_path.parent.mkdir(parents=True)
            Image.new("RGB", (4, 4), "white").save(baseline_path)
            baseline_before = baseline_path.read_bytes()
            current = work / "current.png"
            Image.new("RGB", (5, 4), "black").save(current)

            result = self.run_loop(
                work,
                loop_dir,
                "--update-baseline",
                str(current),
                "home",
            )

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["code"], "image_size_mismatch")
            self.assertFalse(payload["baseline_updated"])
            self.assertEqual(baseline_path.read_bytes(), baseline_before)
            bundle = Path(payload["artifacts"]["bundle"])
            self.assertTrue((bundle / "report.json").is_file())
            self.assertTrue((bundle / "manifest.json").is_file())
            self.assertFalse((bundle / "diff.png").exists())
            self.assertEqual(
                Path(payload["artifacts"]["report"]).parent,
                (loop_dir / "reports").resolve(),
            )

    def test_create_replaces_orphan_sidecar_with_matching_capture_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            loop_dir = work / "loop"
            baseline_path = loop_dir / "baselines" / "home.png"
            baseline_path.parent.mkdir(parents=True)
            Path(f"{baseline_path}.seer.json").write_text('{"stale":true}', encoding="utf-8")
            current = work / "current.png"
            Image.new("RGB", (4, 4), "white").save(current)
            sidecar = write_capture_sidecar(current)

            result = self.run_loop(work, loop_dir, "--create-baseline", str(current), "home")

            self.assertEqual(result.returncode, 0, result.stderr)
            baseline_sidecar = Path(f"{baseline_path}.seer.json")
            self.assertEqual(json.loads(baseline_sidecar.read_text(encoding="utf-8")), json.loads(sidecar.read_text(encoding="utf-8")))
            payload = json.loads(result.stdout)
            manifest = json.loads(Path(payload["artifacts"]["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["images"]["baseline"]["capture_metadata"]["status"], "verified")

    def test_cli_preserves_structured_invalid_mask_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            loop_dir = work / "loop"
            current = work / "current.png"
            Image.new("RGB", (4, 4), "white").save(current)

            malformed = self.run_cli(
                work,
                loop_dir,
                str(current),
                "home",
                "--ignore-rect",
                "bad",
            )
            self.assertEqual(malformed.returncode, 2, malformed.stderr)
            malformed_payload = json.loads(malformed.stdout)
            self.assertEqual(malformed_payload["operation"], "verify")
            self.assertEqual(malformed_payload["error"]["code"], "invalid_arguments")

            excluded = self.run_cli(
                work,
                loop_dir,
                str(current),
                "home",
                "--create-baseline",
                "--ignore-rect",
                "0,0,4,4",
            )
            self.assertEqual(excluded.returncode, 2, excluded.stderr)
            excluded_payload = json.loads(excluded.stdout)
            self.assertEqual(excluded_payload["operation"], "verify")
            self.assertEqual(excluded_payload["error"]["code"], "invalid_arguments")
            self.assertFalse(loop_dir.exists())


if __name__ == "__main__":
    unittest.main()
