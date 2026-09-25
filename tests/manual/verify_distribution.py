#!/usr/bin/env python3
"""Evaluate isolated local Seer installs and synthetic visual verdicts.

This is a staged-copy check, not a network install or a Codex/Claude host test.
It reads the v0.8 package from Git and the candidate package from this checkout;
all staged files, synthetic PNGs, and reports stay under .seer/qa/.
"""
import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

try:
    from PIL import Image, __version__ as PILLOW_VERSION
except ImportError as exc:
    raise SystemExit("Pillow is required; install Pillow 11.x for Python 3.9 or the MCP requirements for Python 3.13") from exc


ROOT = Path(__file__).resolve().parents[2]
QA_ROOT = ROOT / ".seer" / "qa"
V08_REF = "097198e26a114dafd6b160a5e31b8b9fbf4eab92"
REQUIRED_SKILL_FILES = (
    "SKILL.md",
    "scripts/seer",
    "scripts/result_guidance.py",
    "scripts/compare_images.py",
    "scripts/loop_compare.sh",
    "scripts/requirements-mcp.txt",
    "scripts/seer_native.swift",
)
REQUIRED_CANDIDATE_FILES = (
    "scripts/evidence_tools.py",
    "scripts/seer_version.py",
)
STATUS_EXIT = {"pass": 0, "fail": 1, "error": 2, "needs_baseline": 3}


class EvaluationError(RuntimeError):
    pass


def run(command, *, cwd, env=None, timeout=60):
    command = [str(part) for part in command]
    try:
        return subprocess.run(
            command, cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        message = "process exceeded {0} seconds".format(timeout)
        return subprocess.CompletedProcess(command, 124, stdout, (stderr + "\n" + message).strip())


def git_output(*args):
    result = run(["git", *args], cwd=ROOT, timeout=30)
    if result.returncode:
        raise EvaluationError("git " + " ".join(args) + " failed: " + result.stderr.strip())
    return result.stdout.strip()


def tree_manifest(directory):
    manifest = {}
    for path in sorted(Path(directory).rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise EvaluationError("unexpected symlink in staged package: " + str(path))
        if path.is_file():
            data = path.read_bytes()
            manifest[path.relative_to(directory).as_posix()] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "mode": path.stat().st_mode & 0o777,
            }
    return manifest


def tree_digest(directory):
    manifest = tree_manifest(directory)
    serialized = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def extract_git_archive(ref, destination):
    result = subprocess.run(
        ["git", "archive", "--format=tar", ref, "skills/seer", ".claude-plugin"],
        cwd=str(ROOT), capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise EvaluationError("could not read v0.8 Git archive: " + message)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    allowed = ("skills/seer/", ".claude-plugin/")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if not any(member.name.startswith(prefix) for prefix in allowed):
                continue
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                raise EvaluationError("unsafe or unsupported path in Git archive: " + member.name)
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise EvaluationError("could not read archived file: " + member.name)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)


def check(condition, label, report, details=None, *, fatal=True):
    entry = {"label": label, "passed": bool(condition)}
    if details:
        entry.update(details)
    report["checks"].append(entry)
    if not condition:
        report["failures"].append(label)
        if fatal:
            raise EvaluationError(label)
    return bool(condition)


def environment_for(project):
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["SEER_OUT_DIR"] = str(project / ".seer")
    env["SEER_LOOP_DIR"] = str(project / ".seer" / "loop")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def validate_plugin_root(plugin_root):
    plugin_root = Path(plugin_root)
    metadata_dir = plugin_root / ".claude-plugin"
    plugin = json.loads((metadata_dir / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((metadata_dir / "marketplace.json").read_text(encoding="utf-8"))
    entries = [item for item in marketplace.get("plugins", []) if item.get("name") == plugin.get("name")]
    if len(entries) != 1 or entries[0].get("source") != "./":
        raise EvaluationError("Claude marketplace metadata must point to exactly one root plugin")
    skill_paths = plugin.get("skills")
    if not isinstance(skill_paths, list) or not skill_paths:
        raise EvaluationError("Claude plugin metadata has no skill paths")
    resolved = []
    for relative in skill_paths:
        if not isinstance(relative, str):
            raise EvaluationError("Claude plugin skill paths must be strings")
        target = (plugin_root / relative).resolve()
        if not target.is_relative_to(plugin_root.resolve()) or not target.is_dir():
            raise EvaluationError("Claude plugin skill path is missing or escapes its local root: " + relative)
        resolved.append(str(target.relative_to(plugin_root.resolve())))
    return {"name": plugin.get("name"), "version": plugin.get("version"), "skills": resolved}


def verify_installed_skill(label, skill_root, report, plugin_root=None, *, candidate=False):
    skill_root = Path(skill_root)
    required_files = REQUIRED_SKILL_FILES + (REQUIRED_CANDIDATE_FILES if candidate else ())
    missing = [path for path in required_files if not (skill_root / path).is_file()]
    check(not missing, label + " contains the required skill files", report,
          {"missing_files": missing}, fatal=True)
    if plugin_root is not None:
        plugin = validate_plugin_root(plugin_root)
        check(True, label + " Claude plugin metadata resolves locally", report, {"plugin": plugin})

    cli = skill_root / "scripts" / "seer"
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    help_result = run([cli, "--help"], cwd=ROOT, env=env)
    check(help_result.returncode == 0 and "usage: seer" in help_result.stdout,
          label + " CLI help", report,
          {"exit_code": help_result.returncode, "stderr": help_result.stderr.strip()})

    probe = (
        "import runpy, sys; from pathlib import Path; "
        "p=Path(sys.argv[1]).resolve(); sys.path.insert(0, str(p.parent)); "
        "m=runpy.run_path(str(p), run_name='seer_distribution_import'); "
        "assert callable(m.get('main')); import result_guidance; import compare_images"
    )
    if candidate:
        probe += "; import evidence_tools, seer_version; assert seer_version.SEER_VERSION == '0.9.0'"
    import_result = run([sys.executable, "-c", probe, cli], cwd=ROOT, env=env)
    check(import_result.returncode == 0, label + " CLI and comparison imports", report,
          {"exit_code": import_result.returncode, "stderr": import_result.stderr.strip()})
    if candidate:
        diagnostic_result = run([cli, "diagnostics", "--json"], cwd=ROOT, env=env)
        diagnostic_payload = parse_cli_result(diagnostic_result, label + " diagnostics")
        check(diagnostic_result.returncode == 0
              and diagnostic_payload.get("operation") == "diagnostics"
              and diagnostic_payload.get("status") == "pass"
              and diagnostic_payload.get("seer_version") == "0.9.0",
              label + " v0.9 diagnostics", report,
              {"exit_code": diagnostic_result.returncode,
               "operation": diagnostic_payload.get("operation"),
               "status": diagnostic_payload.get("status"),
               "seer_version": diagnostic_payload.get("seer_version")})
    return tree_digest(skill_root)


def invoke_cli(skill_root, project, arguments):
    return run([Path(skill_root) / "scripts" / "seer", *arguments],
               cwd=project, env=environment_for(project))


def parse_cli_result(result, label):
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise EvaluationError(label + " returned invalid JSON: " + result.stdout[:200]) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
        raise EvaluationError(label + " returned an invalid result object")
    return payload


def make_image(path, size=(8, 8), changed_pixel=None):
    image = Image.new("RGB", size, (43, 112, 176))
    if changed_pixel is not None:
        image.putpixel(changed_pixel, (255, 255, 255))
    image.save(path, format="PNG")
    return Path(path)


def storage_digest(directory):
    directory = Path(directory)
    if not directory.exists():
        return None
    return tree_digest(directory)


def write_markdown(report, path):
    evaluation = report.get("evaluation", {})
    lines = [
        "# Seer distribution evaluation",
        "",
        "**Status:** " + report.get("status", "error"),
        "",
        "This run staged local package copies under `.seer/qa`, using the documented Codex skill path and Claude plugin metadata. It did not contact GitHub, run `$skill-installer`, install a Claude plugin, or touch `~/.codex` or `~/.claude`. All images are synthetic.",
        "",
        "## Evaluation",
        "",
        "- Scenarios: {scenario_count} across {repetitions} repetitions".format(**evaluation),
        "- Correct verdicts: {correct_count}/{scenario_count} ({correct_verdict_success_rate:.1%})".format(**evaluation),
        "- Expected non-pass cases: {expected_nonpass_count}".format(**evaluation),
        "- False passes: {false_pass_count}/{expected_nonpass_count} ({false_pass_rate:.1%})".format(**evaluation),
        "- Elapsed time measured with `time.monotonic`: {duration_seconds:.3f}s".format(**report),
        "",
        "## Environment and source",
        "",
        "- Python: `{python_version}` (`{python_executable}`)".format(**report["environment"]),
        "- Pillow: `{pillow_version}`".format(**report["environment"]),
        "- OS: `{platform}`".format(**report["environment"]),
        "- v0.8 source: `{baseline_ref}`".format(**report["source"]),
        "- Candidate source commit: `{candidate_commit}`".format(**report["source"]),
        "- Candidate skill tree SHA-256: `{candidate_skill_sha256}`".format(**report["source"]),
        "",
        "## Verdicts",
        "",
        "| Run | Scenario | Expected | Actual | Exit | Seconds | Correct |",
        "|---:|---|---|---|---:|---:|:---:|",
    ]
    for row in report.get("scenarios", []):
        lines.append("| {repeat} | {scenario} | {expected} | {actual} | {exit_code} | {elapsed_seconds:.3f} | {correct} |".format(**row))
    lines.extend(["", "## Install and update checks", "", "| Check | Result | Details |", "|---|:---:|---|"])
    for item in report.get("checks", []):
        details = {key: value for key, value in item.items() if key not in ("label", "passed")}
        lines.append("| {label} | {result} | {details} |".format(
            label=item["label"], result="pass" if item["passed"] else "fail",
            details=json.dumps(details, sort_keys=True, ensure_ascii=False) if details else "",
        ))
    if report.get("error"):
        lines.extend(["", "## Error", "", "```text", report["error"], "```"])
    lines.extend(["", "Reports and staged evidence: `" + str(path.parent) + "`", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3,
                        help="repeat each synthetic verification scenario (default: 3; maximum: 20)")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 20:
        parser.error("--repetitions must be between 1 and 20")
    return args


def main():
    args = parse_args()
    QA_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="distribution-check-", dir=str(QA_ROOT)))
    started = time.monotonic()
    report = {
        "schema_version": 1,
        "status": "error",
        "method": "isolated local staged copies; no network or host installer invoked",
        "installation_boundary": {
            "codex": "copy of skills/seer into .seer/qa staging path matching README's requested skill path",
            "claude": "local .claude-plugin metadata plus skills/seer staged under .seer/qa",
            "user_installations_touched": False,
            "synthetic_images_only": True,
        },
        "repetitions": args.repetitions,
        "checks": [],
        "scenarios": [],
        "failures": [],
        "evidence_directory": str(run_dir),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "baseline_ref": V08_REF,
            "candidate_commit": "unavailable",
            "candidate_source": str(ROOT / "skills" / "seer"),
            "candidate_skill_sha256": "unavailable",
            "candidate_files": 0,
            "baseline_archive_method": "git archive (read-only)",
        },
        "environment": {
            "python_version": sys.version.split()[0],
            "python_executable": str(Path(sys.executable).resolve()),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "mac_version": platform.mac_ver()[0] or None,
            "machine": platform.machine(),
            "pillow_version": PILLOW_VERSION,
        },
    }
    try:
        source_commit = git_output("rev-parse", "HEAD")
        checkout_skill = ROOT / "skills" / "seer"
        candidate_manifest = tree_manifest(checkout_skill)
        candidate_digest = tree_digest(checkout_skill)
        candidate_source = run_dir / "candidate-source" / "skills" / "seer"
        candidate_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(checkout_skill, candidate_source,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        snapshot_digest = tree_digest(candidate_source)
        check(snapshot_digest == candidate_digest, "candidate source snapshot matches the checkout", report,
              {"checkout_sha256": candidate_digest, "snapshot_sha256": snapshot_digest})
        report["source"] = {
            "baseline_ref": V08_REF,
            "candidate_commit": source_commit,
            "candidate_source": str(checkout_skill),
            "candidate_snapshot": str(candidate_source),
            "candidate_skill_sha256": candidate_digest,
            "candidate_files": len(candidate_manifest),
            "baseline_archive_method": "git archive (read-only)",
        }

        install_root = run_dir / "install"
        claude_root = install_root / "claude-plugin"
        codex_root = install_root / "codex"
        extract_git_archive(V08_REF, claude_root)
        old_claude_skill = claude_root / "skills" / "seer"
        old_codex_skill = codex_root / "skills" / "seer"
        old_codex_skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old_claude_skill, old_codex_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        old_digest_claude = verify_installed_skill(
            "v0.8 Claude staged install", old_claude_skill, report, claude_root)
        old_digest_codex = verify_installed_skill(
            "v0.8 Codex staged install", old_codex_skill, report)
        check(old_digest_claude == old_digest_codex, "v0.8 Codex and Claude package contents match", report)

        project = run_dir / "synthetic-project"
        project.mkdir()
        baseline_image = make_image(project / "base.png")
        loop_dir = project / ".seer" / "loop"
        missing = invoke_cli(old_codex_skill, project, ["verify", str(baseline_image), "not-approved", "--json"])
        missing_payload = parse_cli_result(missing, "v0.8 missing-baseline check")
        check(missing.returncode == 3 and missing_payload.get("status") == "needs_baseline",
              "v0.8 first verification requests a baseline", report,
              {"exit_code": missing.returncode, "status": missing_payload.get("status")})
        check(not loop_dir.exists(), "missing baseline leaves verification storage untouched", report)

        create = invoke_cli(old_codex_skill, project, [
            "verify", str(baseline_image), "approved", "--create-baseline", "--json"])
        create_payload = parse_cli_result(create, "v0.8 synthetic baseline creation")
        baseline_path = loop_dir / "baselines" / "approved.png"
        check(create.returncode == 0 and create_payload.get("status") == "pass" and baseline_path.is_file(),
              "v0.8 explicitly approved synthetic baseline creation", report,
              {"exit_code": create.returncode, "status": create_payload.get("status")})
        baseline_sha = hashlib.sha256(baseline_path.read_bytes()).hexdigest()

        # Preserve the staged v0.8 package, then replace the same install paths
        # with the current checkout to model a local update without host writes.
        shutil.copytree(claude_root, install_root / "claude-plugin-before-update")
        shutil.copytree(codex_root, install_root / "codex-before-update")
        for target, source in ((old_claude_skill, candidate_source), (old_codex_skill, candidate_source)):
            staged = target.with_name(target.name + ".staged")
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.rmtree(target)
            staged.rename(target)
        current_metadata = ROOT / ".claude-plugin"
        staged_metadata = install_root / "claude-plugin" / ".claude-plugin.staged"
        shutil.copytree(current_metadata, staged_metadata)
        shutil.rmtree(claude_root / ".claude-plugin")
        staged_metadata.rename(claude_root / ".claude-plugin")

        updated_digest_codex = verify_installed_skill(
            "updated Codex staged install", old_codex_skill, report, candidate=True)
        updated_digest_claude = verify_installed_skill(
            "updated Claude staged install", old_claude_skill, report, claude_root, candidate=True)
        check(updated_digest_codex == candidate_digest == updated_digest_claude,
              "updated staged packages match the current skill tree", report)
        check(hashlib.sha256(baseline_path.read_bytes()).hexdigest() == baseline_sha,
              "update preserves the v0.8 approved baseline byte-for-byte", report)

        outside = make_image(project / "outside-mask.png", changed_pixel=(7, 7))
        ignored = make_image(project / "ignored-change.png", changed_pixel=(1, 1))
        wrong_size = make_image(project / "wrong-size.png", size=(9, 8))
        scenarios = [
            ("identical image passes", baseline_image, "approved", [], "pass", None),
            ("change outside ignored rectangle fails", outside, "approved",
             ["--ignore-rect", "0,0,4,4"], "fail", None),
            ("change inside ignored rectangle passes", ignored, "approved",
             ["--ignore-rect", "0,0,4,4"], "pass", None),
            ("dimension mismatch is an error", wrong_size, "approved", [], "error", "image_size_mismatch"),
            ("missing baseline stays unapproved", baseline_image, "not-approved", [], "needs_baseline", None),
        ]
        for repeat in range(1, args.repetitions + 1):
            for label, image_path, baseline_name, options, expected, expected_error in scenarios:
                before_missing = storage_digest(loop_dir) if baseline_name == "not-approved" else None
                started_case = time.monotonic()
                result = invoke_cli(old_codex_skill, project, [
                    "verify", str(image_path), baseline_name, *options, "--json"])
                elapsed = time.monotonic() - started_case
                payload = parse_cli_result(result, label)
                actual = payload["status"]
                error_code = (payload.get("error") or {}).get("code")
                correct = actual == expected and result.returncode == STATUS_EXIT[expected]
                if expected_error:
                    correct = correct and error_code == expected_error
                if baseline_name == "not-approved":
                    after_missing = storage_digest(loop_dir)
                    correct = correct and before_missing == after_missing
                    correct = correct and not (loop_dir / "baselines" / "not-approved.png").exists()
                correct = correct and hashlib.sha256(baseline_path.read_bytes()).hexdigest() == baseline_sha
                row = {
                    "repeat": repeat,
                    "scenario": label,
                    "expected": expected,
                    "actual": actual,
                    "exit_code": result.returncode,
                    "error_code": error_code,
                    "elapsed_seconds": elapsed,
                    "correct": bool(correct),
                }
                report["scenarios"].append(row)
                check(correct, "run {0}: {1}".format(repeat, label), report,
                      {"expected": expected, "actual": actual, "exit_code": result.returncode,
                       "error_code": error_code, "elapsed_seconds": elapsed}, fatal=False)

        rows = report["scenarios"]
        expected_nonpass = [row for row in rows if row["expected"] != "pass"]
        false_pass_count = sum(row["actual"] == "pass" for row in expected_nonpass)
        correct_count = sum(row["correct"] for row in rows)
        report["evaluation"] = {
            "repetitions": args.repetitions,
            "scenario_count": len(rows),
            "correct_count": correct_count,
            "correct_verdict_success_rate": correct_count / len(rows) if rows else 0.0,
            "expected_nonpass_count": len(expected_nonpass),
            "false_pass_count": false_pass_count,
            "false_pass_rate": false_pass_count / len(expected_nonpass) if expected_nonpass else 0.0,
        }
        check(false_pass_count == 0, "no false passes in expected-nonpass cases", report,
              {"false_pass_count": false_pass_count, "denominator": len(expected_nonpass)}, fatal=False)
        report["status"] = "pass" if not report["failures"] else "fail"
    except Exception as exc:
        report["error"] = "{0}: {1}".format(type(exc).__name__, exc)
        if not report["failures"]:
            report["failures"].append(report["error"])
        report["status"] = "fail"
    finally:
        report["duration_seconds"] = time.monotonic() - started
        report["duration_clock"] = "time.monotonic"
        report["report_json"] = str(run_dir / "report.json")
        report["report_markdown"] = str(run_dir / "report.md")
        if "evaluation" not in report:
            rows = report["scenarios"]
            nonpass = [row for row in rows if row["expected"] != "pass"]
            report["evaluation"] = {
                "repetitions": args.repetitions,
                "scenario_count": len(rows),
                "correct_count": sum(row["correct"] for row in rows),
                "correct_verdict_success_rate": sum(row["correct"] for row in rows) / len(rows) if rows else 0.0,
                "expected_nonpass_count": len(nonpass),
                "false_pass_count": sum(row["actual"] == "pass" for row in nonpass),
                "false_pass_rate": sum(row["actual"] == "pass" for row in nonpass) / len(nonpass) if nonpass else 0.0,
            }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_markdown(report, run_dir / "report.md")
        print(json.dumps({"status": report["status"], "report_json": report["report_json"],
                          "report_markdown": report["report_markdown"],
                          "evaluation": report.get("evaluation")}, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
