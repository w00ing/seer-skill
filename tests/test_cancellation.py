"""Cancellation of the CLI must unwind workers in separate process groups."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/seer/scripts"


@unittest.skipUnless(os.name == "posix", "process-group cancellation requires POSIX")
class CancellationTests(unittest.TestCase):
    def test_termination_reaps_inspection_and_stability_workers(self):
        for kind in ("inspect", "stable"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                worker = work / "worker.py"
                pid_file = work / "pid"
                worker.write_text(
                    "import os,time\nfrom pathlib import Path\n"
                    f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                    "time.sleep(60)\n"
                )
                call = (f"ui_inspect._run_process([sys.executable,{str(worker)!r}],time.monotonic()+60)"
                        if kind == "inspect" else
                        f"stable_wait.capture_sample({str(worker)!r},123,Path({str(work / 'out.png')!r}),60)")
                source = (
                    "import sys,signal,time\nfrom pathlib import Path\n"
                    f"sys.path.insert(0,{str(SCRIPTS)!r})\n"
                    "import ui_inspect,stable_wait\n"
                    "def terminate(sig,frame): raise SystemExit(128+sig)\n"
                    "signal.signal(signal.SIGTERM,terminate)\n" + call
                )
                process = subprocess.Popen([sys.executable, "-c", source], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                worker_pid = None
                try:
                    deadline = time.monotonic() + 10
                    while not pid_file.exists() and time.monotonic() < deadline and process.poll() is None:
                        time.sleep(0.01)
                    self.assertTrue(pid_file.exists(), "nested worker did not start")
                    worker_pid = int(pid_file.read_text())
                    process.terminate()
                    _, stderr = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 143, stderr)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(worker_pid, 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                    if worker_pid:
                        try:
                            os.killpg(worker_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
