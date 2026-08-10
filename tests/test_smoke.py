#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "seer" / "scripts"


class SmokeTests(unittest.TestCase):
    def write_executable(self, path: Path, source: str):
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def run_script(self, script: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def run_shell(
        self, script: str, *args: str, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPTS / script), *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )

    def run_cli(
        self, *args: str, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "seer"), *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_cli_machine_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            source = work / "source.png"
            current = work / "current.png"
            loop_dir = work / "loop"
            Image.new("RGB", (4, 4), "white").save(source)
            self.write_executable(
                bin_dir / "osascript",
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *JavaScript*) printf '%s\\n' '{\"windows\":[{\"process\":\"FakeApp\",\"index\":1,\"title\":\"A \\\"quoted\\\" window\",\"frontmost\":true,\"bounds\":{\"x\":1,\"y\":2,\"width\":4,\"height\":4}}],\"visible_processes\":1,\"accessible_processes\":1,\"skipped_processes\":0,\"skipped_windows\":0,\"frontmost_process\":\"FakeApp\"}' ;;\n"
                "  *visible*) echo FakeApp ;;\n"
                "  *frontmost*) echo FakeApp ;;\n"
                "  *) script=$(cat); case \"$script\" in *position*) echo '1, 2' ;; *size*) echo '4, 4' ;; esac ;;\n"
                "esac\n",
            )
            self.write_executable(
                bin_dir / "screencapture",
                "#!/usr/bin/env bash\n"
                "for arg in \"$@\"; do output=$arg; done\n"
                "mkdir -p \"$(dirname \"$output\")\"\n"
                "cp \"$SEER_TEST_IMAGE\" \"$output\"\n",
            )
            env = os.environ | {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "SEER_OUT_DIR": str(work / "out"),
                "SEER_LOOP_DIR": str(loop_dir),
                "SEER_TEST_IMAGE": str(source),
            }

            doctor = self.run_cli("doctor", "--json", cwd=work, env=env)
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            self.assertEqual(json.loads(doctor.stdout)["status"], "pass")
            self.assertEqual(doctor.stderr, "")

            windows = self.run_cli("windows", "--json", cwd=work, env=env)
            windows_payload = json.loads(windows.stdout)
            self.assertEqual(windows.returncode, 0, windows.stderr)
            self.assertEqual(windows_payload["windows"][0]["title"], 'A "quoted" window')

            captured = self.run_cli(
                "capture",
                "--process",
                "FakeApp",
                "--out",
                str(current),
                "--json",
                cwd=work,
                env=env,
            )
            captured_payload = json.loads(captured.stdout)
            self.assertEqual(captured.returncode, 0, captured.stderr)
            self.assertEqual(Path(captured_payload["artifacts"]["current"]), current.resolve())
            self.assertTrue(current.is_file())

            missing = self.run_cli("verify", str(current), "home", "--json", cwd=work, env=env)
            self.assertEqual(missing.returncode, 3, missing.stderr)
            self.assertEqual(json.loads(missing.stdout)["status"], "needs_baseline")

            created = self.run_cli(
                "verify",
                str(current),
                "home",
                "--create-baseline",
                "--json",
                cwd=work,
                env=env,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(json.loads(created.stdout)["operation"], "baseline_create")

            changed = Image.new("RGB", (4, 4), "white")
            changed.putpixel((0, 0), (0, 0, 0))
            changed.save(current)
            failed = self.run_cli("verify", str(current), "home", "--json", cwd=work, env=env)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertEqual(json.loads(failed.stdout)["status"], "fail")

            invalid = self.run_cli(
                "verify",
                str(current),
                "home",
                "--max-diff-percent",
                "nan",
                "--json",
                cwd=work,
                env=env,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, "")
            self.assertIn("finite number", invalid.stderr)

    def test_compare_detects_alpha_and_writes_basename_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            Image.new("RGBA", (1, 1), (0, 0, 0, 255)).save(work / "base.png")
            Image.new("RGBA", (1, 1), (0, 0, 0, 254)).save(work / "current.png")

            result = self.run_script(
                "compare_images.py",
                "base.png",
                "current.png",
                "--diff-out",
                "diff.png",
                "--json-out",
                "report.json",
                cwd=work,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["percent_changed"], 100.0)
            self.assertTrue((work / "diff.png").is_file())
            self.assertTrue((work / "report.json").is_file())

    def test_loop_compare_baseline_and_threshold_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            current = work / "current.png"
            loop_dir = work / "loop"
            baseline = loop_dir / "baselines" / "home.png"
            Image.new("RGB", (4, 4), "white").save(current)
            env = os.environ | {"SEER_LOOP_DIR": str(loop_dir)}

            missing = self.run_shell(
                "loop_compare.sh", str(current), "home", cwd=work, env=env
            )

            self.assertEqual(missing.returncode, 3, missing.stderr)
            self.assertEqual(json.loads(missing.stdout)["status"], "needs_baseline")
            self.assertFalse(loop_dir.exists())

            current.write_bytes(b"not an image")
            invalid_baseline = self.run_shell(
                "loop_compare.sh",
                "--create-baseline",
                str(current),
                "home",
                cwd=work,
                env=env,
            )
            self.assertEqual(invalid_baseline.returncode, 2)
            self.assertFalse(loop_dir.exists())
            Image.new("RGB", (4, 4), "white").save(current)

            created = self.run_shell(
                "loop_compare.sh",
                "--create-baseline",
                str(current),
                "home",
                cwd=work,
                env=env,
            )

            created_payload = json.loads(created.stdout)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(created_payload["operation"], "baseline_create")
            self.assertEqual(created_payload["status"], "pass")
            self.assertEqual(
                Path(created_payload["baseline_created"]).resolve(), baseline.resolve()
            )
            self.assertTrue(baseline.is_file())

            compared = self.run_shell(
                "loop_compare.sh", str(current), "home", cwd=work, env=env
            )
            compared_payload = json.loads(compared.stdout)
            self.assertEqual(compared.returncode, 0, compared.stderr)
            self.assertEqual(compared_payload["status"], "pass")
            self.assertEqual(compared_payload["percent_changed"], 0.0)

            changed_image = Image.new("RGB", (4, 4), "white")
            changed_image.putpixel((0, 0), (0, 0, 0))
            changed_image.save(current)

            failed = self.run_shell(
                "loop_compare.sh", str(current), "home", cwd=work, env=env
            )
            failed_payload = json.loads(failed.stdout)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertEqual(failed_payload["status"], "fail")
            self.assertEqual(failed_payload["pixels_changed"], 1)

            allowed = self.run_shell(
                "loop_compare.sh",
                "--max-diff-percent",
                "6.25",
                str(current),
                "home",
                cwd=work,
                env=env,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(json.loads(allowed.stdout)["status"], "pass")

            invalid = self.run_shell(
                "loop_compare.sh",
                "--max-diff-percent",
                "nan",
                str(current),
                "home",
                cwd=work,
                env=env,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, "")

            baseline_before = baseline.read_bytes()
            history_count = len(list((loop_dir / "history").iterdir()))
            current.write_bytes(b"not an image")
            corrupt = self.run_shell(
                "loop_compare.sh",
                "--update-baseline",
                str(current),
                "home",
                cwd=work,
                env=env,
            )
            self.assertEqual(corrupt.returncode, 2)
            self.assertEqual(corrupt.stdout, "")
            self.assertEqual(baseline.read_bytes(), baseline_before)
            self.assertEqual(len(list((loop_dir / "history").iterdir())), history_count)

    def test_annotate_rejects_invalid_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            Image.new("RGB", (4, 4), "white").save(work / "input.png")
            (work / "spec.json").write_text(
                json.dumps({"annotations": [{"type": "rect", "color": "invalid"}]}),
                encoding="utf-8",
            )

            result = self.run_script(
                "annotate_image.py",
                "input.png",
                "output.png",
                "--spec",
                "spec.json",
                cwd=work,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((work / "output.png").exists())

    def test_excalidraw_writes_basename_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            result = self.run_script(
                "excalidraw_from_text.py",
                "--text",
                "header: Test",
                "--out",
                "wire.excalidraw",
                cwd=work,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((work / "wire.excalidraw").is_file())

    def test_summarize_validates_before_touching_existing_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            for command in ("ffmpeg", "ffprobe"):
                self.write_executable(bin_dir / command, "#!/usr/bin/env bash\nexit 0\n")

            video = work / "input.mov"
            video.write_bytes(b"video")
            out_dir = work / "summary"
            out_dir.mkdir()
            sentinel = out_dir / "frame-0001.png"
            sentinel.write_bytes(b"keep")
            env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

            for option, value in (("--mode", "invalid"), ("--max", "nope")):
                with self.subTest(option=option):
                    result = self.run_shell(
                        "summarize_video.sh",
                        str(video),
                        "--out",
                        str(out_dir),
                        option,
                        value,
                        cwd=work,
                        env=env,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_record_propagates_summary_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            self.write_executable(
                bin_dir / "osascript",
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *frontmost*) echo FakeApp ;;\n"
                "  *position*) echo '0, 0' ;;\n"
                "  *size*) echo '10, 10' ;;\n"
                "esac\n",
            )
            self.write_executable(
                bin_dir / "screencapture",
                "#!/usr/bin/env bash\n"
                "for arg in \"$@\"; do output=$arg; done\n"
                "mkdir -p \"$(dirname \"$output\")\"\n"
                ": > \"$output\"\n",
            )
            self.write_executable(bin_dir / "ffmpeg", "#!/usr/bin/env bash\nexit 7\n")
            self.write_executable(bin_dir / "ffprobe", "#!/usr/bin/env bash\nexit 0\n")
            env = os.environ | {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "SEER_OUT_DIR": str(work / "out"),
            }

            result = self.run_shell(
                "record_app_window.sh", "--duration", "0", "--summary", cwd=work, env=env
            )

            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn("summarize_video.sh failed (exit 7)", result.stderr)

    def test_capture_keeps_errors_off_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            self.write_executable(bin_dir / "osascript", "#!/usr/bin/env bash\nexit 1\n")
            env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

            result = self.run_shell("capture_app_window.sh", cwd=work, env=env)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("window not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
