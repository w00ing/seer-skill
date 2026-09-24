#!/usr/bin/env python3
"""Compatibility implementation for Seer's visual baseline loop.

The shell entry point stays in place for existing callers, while this module
owns the evidence bundle and baseline lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from compare_images import ComparisonError, compare_images, parse_rect
except ImportError:  # Keep the entry point useful if Pillow is unavailable.
    ComparisonError = None  # type: ignore[assignment,misc]
    compare_images = None  # type: ignore[assignment]
    parse_rect = None  # type: ignore[assignment]

USAGE = """loop_compare.sh

Usage:
  loop_compare.sh [options] <current_path> <baseline_name>

Options:
  --loop-dir <path>   Override loop storage directory (default: $SEER_LOOP_DIR or .seer/loop)
  --resize            Resize current image to match baseline size
  --ignore-rect X,Y,W,H
                      Ignore this rectangle while comparing (repeatable)
  --max-diff-percent <n>
                      Maximum changed pixels allowed, 0-100 (default: 0)
  --create-baseline   Create a missing baseline from current
  --update-baseline   Replace baseline with current after comparison
  -h, --help          Show help

Behavior:
  - Stores latest, history, and diff images under the loop directory
  - Returns needs_baseline (exit 3) when the baseline is missing
  - Creates baselines only with --create-baseline
"""


class UsageError(Exception):
    pass


def _safe_name(name: str) -> str:
    # Mirrors the original shell pipeline: tr ' /:' '___', then retain only
    # ASCII letters, digits, dot, underscore, and hyphen.
    translated = name.translate(str.maketrans({" ": "_", "/": "_", ":": "_"}))
    safe = "".join(ch for ch in translated if ch.isascii() and (ch.isalnum() or ch in "._-"))
    return safe or "baseline"


def _default_loop_dir() -> Path:
    out_root = os.environ.get("SEER_OUT_DIR") or os.environ.get("SEER_TMP_DIR") or ".seer"
    explicit = os.environ.get("SEER_LOOP_DIR")
    if explicit:
        return Path(explicit)

    loop_dir = Path(out_root) / "loop"
    # Backward compatibility: an established legacy baseline layout wins if
    # the new nested layout has not yet been created.
    if (Path(out_root) / "baselines").is_dir() and not (loop_dir / "baselines").is_dir():
        return Path(out_root)
    return loop_dir


def _parse_args(argv: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "loop_dir": _default_loop_dir(),
        "resize": False,
        "max_diff_percent": "0",
        "create_baseline": False,
        "update_baseline": False,
        "ignore_rect_text": [],
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--loop-dir":
            if index + 1 >= len(argv):
                raise UsageError("error: --loop-dir requires a value")
            options["loop_dir"] = Path(argv[index + 1])
            index += 2
        elif arg == "--resize":
            options["resize"] = True
            index += 1
        elif arg == "--ignore-rect":
            if index + 1 >= len(argv):
                raise UsageError("error: --ignore-rect requires a value")
            options["ignore_rect_text"].append(argv[index + 1])
            index += 2
        elif arg == "--max-diff-percent":
            if index + 1 >= len(argv):
                raise UsageError("error: --max-diff-percent requires a value")
            options["max_diff_percent"] = argv[index + 1]
            index += 2
        elif arg == "--create-baseline":
            options["create_baseline"] = True
            index += 1
        elif arg == "--update-baseline":
            options["update_baseline"] = True
            index += 1
        elif arg in ("-h", "--help"):
            print(USAGE)
            raise SystemExit(0)
        elif arg == "--":
            index += 1
            break
        elif arg.startswith("-"):
            raise UsageError(f"error: unknown option: {arg}\n{USAGE}")
        else:
            break

    remaining = argv[index:]
    current = remaining[0] if remaining else ""
    baseline_name = remaining[1] if len(remaining) > 1 else ""
    if not current or not baseline_name:
        raise UsageError(USAGE)
    options["current"] = current
    options["baseline_name"] = baseline_name

    if not os.path.isfile(current):
        raise UsageError(f"error: current image not found: {current}")
    if options["create_baseline"] and options["update_baseline"]:
        raise UsageError("error: --create-baseline and --update-baseline cannot be used together")

    try:
        threshold = float(options["max_diff_percent"])
    except (TypeError, ValueError):
        threshold = math.nan
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise UsageError("error: --max-diff-percent must be a finite number between 0 and 100")
    options["max_diff_percent"] = threshold

    if compare_images is None or parse_rect is None:
        raise UsageError("error: Pillow is required. Install with: python3 -m pip install pillow")
    try:
        options["ignore_rects"] = [parse_rect(raw) for raw in options["ignore_rect_text"]]
    except Exception as exc:
        raise UsageError(f"error: invalid --ignore-rect: {exc}") from exc
    return options


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _image_size(path: Path) -> dict[str, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            return {"width": int(image.width), "height": int(image.height)}
    except Exception as exc:
        raise UsageError(f"error: could not read image: {exc}") from exc


def _capture_info(
    image_path: Path,
    image_hash: str,
    image_size: dict[str, int],
    *,
    sidecar_path: Path | None = None,
) -> tuple[dict[str, Any], bytes | None]:
    sidecar = sidecar_path or Path(f"{image_path}.seer.json")
    base = {
        "status": "unknown",
        "captured_at": None,
        "window_id": None,
        "process": None,
        "size": image_size,
        "dpi": None,
        "reason": None,
        "sidecar_path": None,
        "metadata": None,
    }
    if not sidecar.is_file():
        base["reason"] = "missing_sidecar"
        return base, None
    try:
        raw = sidecar.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        base["reason"] = "invalid_sidecar"
        return base, None

    if not isinstance(payload, dict):
        base["reason"] = "invalid_sidecar"
        return base, None
    if payload.get("schema_version") != 1 or payload.get("source") != "seer.capture":
        base["reason"] = "unrecognized_sidecar"
        return base, None
    if payload.get("image_sha256") != image_hash:
        base["reason"] = "image_sha256_mismatch"
        return base, None
    sidecar_size = payload.get("size")
    if not isinstance(sidecar_size, dict) or sidecar_size != image_size:
        base["reason"] = "size_mismatch"
        return base, None
    captured_at = payload.get("captured_at")
    if not isinstance(captured_at, str) or not captured_at.strip():
        base["reason"] = "invalid_capture_metadata"
        return base, None
    try:
        parsed_capture_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        base["reason"] = "invalid_capture_metadata"
        return base, None
    if parsed_capture_time.tzinfo is None or parsed_capture_time.utcoffset() != timezone.utc.utcoffset(parsed_capture_time):
        base["reason"] = "invalid_capture_metadata"
        return base, None
    window_id = payload.get("window_id")
    process = payload.get("process")
    dpi = payload.get("dpi")
    if window_id is not None and (not isinstance(window_id, int) or isinstance(window_id, bool)):
        base["reason"] = "invalid_capture_metadata"
        return base, None
    if process is not None and not isinstance(process, str):
        base["reason"] = "invalid_capture_metadata"
        return base, None
    if dpi is not None and (
        not isinstance(dpi, list)
        or len(dpi) != 2
        or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0 for value in dpi)
    ):
        base["reason"] = "invalid_capture_metadata"
        return base, None

    base.update(
        {
            "status": "verified",
            "captured_at": captured_at,
            "window_id": window_id,
            "process": process,
            "dpi": dpi,
            "reason": None,
            "metadata": payload,
        }
    )
    return base, raw


def _copy_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _atomic_copy(source: Path, destination: Path, *, replace: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as target, source.open("rb") as original:
            shutil.copyfileobj(original, target)
            target.flush()
            os.fsync(target.fileno())
        if replace:
            os.replace(temp, destination)
        else:
            # Hard-linking a same-directory temporary file installs it only
            # when the approved baseline does not already exist.
            os.link(temp, destination)
            temp.unlink()
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as target:
        json.dump(payload, target, indent=2, sort_keys=False)
        target.write("\n")


def _error_fields(exc: BaseException) -> dict[str, Any]:
    code = getattr(exc, "code", "comparison_error")
    message = getattr(exc, "message", str(exc))
    details = getattr(exc, "details", None)
    result: dict[str, Any] = {"code": str(code), "message": str(message)}
    if details is not None:
        result["details"] = details
    return result


def _make_replay_command(options: dict[str, Any]) -> list[str]:
    command = ["python3", str(SCRIPT_DIR / "compare_images.py"), "baseline.png", "current.png"]
    if options["resize"]:
        command.append("--resize")
    for rect in options["ignore_rect_text"]:
        command.extend(["--ignore-rect", rect])
    command.extend(["--max-diff-percent", str(options["max_diff_percent"])])
    return command


def _emit_error(message: str, *, structured: dict[str, Any] | None = None) -> int:
    print(f"error: {message}", file=sys.stderr)
    if structured is not None:
        print(json.dumps(structured))
    return 2


def run(options: dict[str, Any]) -> int:
    current_source = Path(options["current"]).resolve()
    loop_dir = Path(options["loop_dir"])
    safe_name = _safe_name(options["baseline_name"])
    baseline_path = loop_dir / "baselines" / f"{safe_name}.png"
    latest_path = loop_dir / "latest" / f"{safe_name}.png"

    # Validate decodability and record dimensions before making any output.
    try:
        current_size = _image_size(current_source)
    except UsageError as exc:
        return _emit_error(str(exc).removeprefix("error: "))
    baseline_exists = baseline_path.is_file()
    if not baseline_exists:
        if not options["create_baseline"]:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "operation": "verify",
                        "status": "needs_baseline",
                        "baseline": str(baseline_path.resolve()),
                        "current": str(current_source),
                        "thresholds": {"max_diff_percent": options["max_diff_percent"]},
                        "next_action": "Rerun with --create-baseline after explicit approval.",
                    }
                )
            )
            return 3
        baseline_source = current_source
        baseline_size = current_size
        operation = "baseline_create"
    else:
        if options["create_baseline"]:
            return _emit_error(f"baseline already exists: {baseline_path}; use --update-baseline to replace it")
        baseline_source = baseline_path.resolve()
        try:
            baseline_size = _image_size(baseline_source)
        except UsageError as exc:
            return _emit_error(str(exc).removeprefix("error: "))
        operation = "verify"

    # A fully masked image cannot establish an informative approved baseline.
    # Validate this before any artifact or baseline path is written.
    if operation == "baseline_create":
        try:
            compare_images(
                current_source,
                current_source,
                ignore_rects=options["ignore_rects"],
                resize=options["resize"],
                max_diff_percent=options["max_diff_percent"],
            )
        except Exception as exc:
            error = _error_fields(exc)
            return _emit_error(
                error["message"],
                structured={
                    "schema_version": 1,
                    "operation": "verify",
                    "status": "error",
                    "error": error,
                },
            )

    local_now = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    legacy_stamp = f"{local_now}-{os.getpid()}"
    run_id = f"{safe_name}-{local_now}-{uuid.uuid4().hex[:12]}"
    run_dir = loop_dir / "runs" / run_id
    bundle_baseline = run_dir / "baseline.png"
    bundle_current = run_dir / "current.png"
    bundle_diff = run_dir / "diff.png"
    bundle_report = run_dir / "report.json"
    manifest_path = run_dir / "manifest.json"
    history_path = loop_dir / "history" / f"{safe_name}-{legacy_stamp}.png"
    legacy_diff_path = loop_dir / "diffs" / f"{safe_name}-{legacy_stamp}.png"
    legacy_report_path = loop_dir / "reports" / f"{safe_name}-{legacy_stamp}.json"

    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        _copy_snapshot(baseline_source, bundle_baseline)
        _copy_snapshot(current_source, bundle_current)
        baseline_snapshot_size = _image_size(bundle_baseline)
        current_snapshot_size = _image_size(bundle_current)
        baseline_capture, baseline_sidecar_bytes = _capture_info(
            baseline_source,
            _sha256(bundle_baseline),
            baseline_snapshot_size,
        )
        current_capture, current_sidecar_bytes = _capture_info(
            current_source,
            _sha256(bundle_current),
            current_snapshot_size,
        )
        baseline_size = baseline_snapshot_size
        current_size = current_snapshot_size
        if baseline_sidecar_bytes is not None:
            Path(f"{bundle_baseline}.seer.json").write_bytes(baseline_sidecar_bytes)
            baseline_capture["sidecar_path"] = "baseline.png.seer.json"
        if current_sidecar_bytes is not None:
            Path(f"{bundle_current}.seer.json").write_bytes(current_sidecar_bytes)
            current_capture["sidecar_path"] = "current.png.seer.json"
    except (OSError, UsageError) as exc:
        shutil.rmtree(run_dir, ignore_errors=True)
        return _emit_error(f"could not create evidence bundle: {exc}")

    comparison: dict[str, Any] | None = None
    comparison_error: dict[str, Any] | None = None
    try:
        comparison = compare_images(
            bundle_baseline,
            bundle_current,
            ignore_rects=options["ignore_rects"],
            resize=options["resize"],
            max_diff_percent=options["max_diff_percent"],
            diff_out=bundle_diff,
        )
    except Exception as exc:
        comparison_error = _error_fields(exc)

    baseline_updated = False
    baseline_created_on_disk = False
    if comparison_error is None and operation == "baseline_create":
        try:
            _atomic_copy(bundle_baseline, baseline_path, replace=False)
            baseline_created_on_disk = True
            target_sidecar = Path(f"{baseline_path}.seer.json")
            try:
                if baseline_sidecar_bytes is not None:
                    _atomic_copy(Path(f"{bundle_baseline}.seer.json"), target_sidecar, replace=True)
                else:
                    target_sidecar.unlink(missing_ok=True)
            except OSError:
                try:
                    baseline_path.unlink(missing_ok=True)
                    target_sidecar.unlink(missing_ok=True)
                    baseline_created_on_disk = False
                finally:
                    raise
        except FileExistsError:
            comparison_error = {
                "code": "baseline_exists",
                "message": f"baseline already exists: {baseline_path}; use --update-baseline to replace it",
            }
        except OSError as exc:
            comparison_error = {"code": "filesystem_error", "message": f"could not create baseline: {exc}"}
    elif comparison_error is None and options["update_baseline"]:
        try:
            _atomic_copy(bundle_current, baseline_path, replace=True)
            baseline_updated = True
            target_sidecar = Path(f"{baseline_path}.seer.json")
            if current_sidecar_bytes is not None:
                _atomic_copy(Path(f"{bundle_current}.seer.json"), target_sidecar, replace=True)
            else:
                target_sidecar.unlink(missing_ok=True)
        except OSError as exc:
            comparison_error = {"code": "filesystem_error", "message": f"could not update baseline: {exc}"}

    # Preserve the legacy output locations for successful pass/fail decisions.
    latest_artifact: str | None = None
    history_artifact: str | None = None
    legacy_diff_artifact: str | None = None
    if comparison_error is None:
        try:
            latest_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle_current, latest_path)
            shutil.copyfile(bundle_current, history_path)
            latest_artifact = str(latest_path.resolve())
            history_artifact = str(history_path.resolve())
            if bundle_diff.is_file():
                legacy_diff_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundle_diff, legacy_diff_path)
                legacy_diff_artifact = str(legacy_diff_path.resolve())
        except OSError as exc:
            comparison_error = {"code": "filesystem_error", "message": f"could not update compatibility artifacts: {exc}"}

    artifacts = {
        "baseline": str(bundle_baseline.resolve()),
        "current": str(bundle_current.resolve()),
        "diff": str(bundle_diff.resolve()) if bundle_diff.is_file() else None,
        "report": str(legacy_report_path.resolve()),
        "bundle_report": str(bundle_report.resolve()),
        "manifest": str(manifest_path.resolve()),
        "latest": latest_artifact,
        "history": history_artifact,
        "legacy_diff": legacy_diff_artifact,
        "bundle": str(run_dir.resolve()),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "operation": "verify" if comparison_error else operation,
        "status": "error" if comparison_error else ("pass" if operation == "baseline_create" else comparison.get("status", "pass")),
        "baseline": str(bundle_baseline.resolve()),
        "current": str(bundle_current.resolve()),
        "diff_image": str(bundle_diff.resolve()) if bundle_diff.is_file() else None,
        "thresholds": {"max_diff_percent": options["max_diff_percent"]},
        "comparison_options": {
            "resize": options["resize"],
            "ignore_rects": list(options["ignore_rect_text"]),
            "max_diff_percent": options["max_diff_percent"],
        },
        "artifacts": artifacts,
        "baseline_updated": baseline_updated,
    }
    if operation == "baseline_create" and baseline_created_on_disk:
        report["baseline_created"] = str(baseline_path.resolve())
    if comparison is not None:
        for key, value in comparison.items():
            if key not in {"baseline", "current", "diff_image", "schema_version", "operation", "status", "thresholds"}:
                report[key] = value
    if comparison_error is not None:
        report["error"] = comparison_error
        error_details = comparison_error.get("details") if comparison_error else None
        report["size_evidence"] = dict(error_details) if isinstance(error_details, dict) else {}
        report["size_evidence"].setdefault("baseline_size", baseline_size)
        report["size_evidence"].setdefault("current_size", current_size)
        report["size_evidence"].setdefault("baseline_capture_dpi", baseline_capture.get("dpi"))
        report["size_evidence"].setdefault("current_capture_dpi", current_capture.get("dpi"))
        report["size_evidence"].setdefault(
            "resized", bool(options["resize"] and baseline_size != current_size)
        )

    # Write a report even when dimensions or scale prevent comparison. It
    # points only at immutable files inside this run's bundle.
    try:
        _write_json(bundle_report, report)
        _write_json(legacy_report_path, report)
    except OSError as exc:
        return _emit_error(f"could not write evidence report: {exc}")

    bundle_files: dict[str, dict[str, Any]] = {}
    file_candidates = {
        "baseline": bundle_baseline,
        "current": bundle_current,
        "report": bundle_report,
    }
    if bundle_diff.is_file():
        file_candidates["diff"] = bundle_diff
    baseline_sidecar_bundle = Path(f"{bundle_baseline}.seer.json")
    current_sidecar_bundle = Path(f"{bundle_current}.seer.json")
    if baseline_sidecar_bundle.is_file():
        file_candidates["baseline_capture_metadata"] = baseline_sidecar_bundle
    if current_sidecar_bundle.is_file():
        file_candidates["current_capture_metadata"] = current_sidecar_bundle
    for role, path in file_candidates.items():
        bundle_files[role] = {
            "path": path.name,
            "sha256": _sha256(path),
            "size_bytes": _file_size(path),
        }

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": _utc_now(),
        "operation": operation,
        "status": report["status"],
        "paths": {role: item["path"] for role, item in bundle_files.items()},
        "files": bundle_files,
        "comparison_options": {
            "resize": options["resize"],
            "ignore_rects": list(options["ignore_rect_text"]),
            "max_diff_percent": options["max_diff_percent"],
        },
        "images": {
            "baseline": {
                "path": "baseline.png",
                "sha256": _sha256(bundle_baseline),
                "source_path": str(baseline_source),
                "size": baseline_size,
                "capture_metadata": baseline_capture,
            },
            "current": {
                "path": "current.png",
                "sha256": _sha256(bundle_current),
                "source_path": str(current_source),
                "size": current_size,
                "capture_metadata": current_capture,
            },
        },
        "comparison": {
            "status": report["status"],
            "error": comparison_error,
            "size_evidence": report.get("size_evidence", comparison.get("scale_evidence") if comparison else None),
            "metrics": {
                key: comparison[key]
                for key in ("pixels_changed", "pixels_total", "percent_changed", "avg_diff_percent", "size", "resized", "scale_evidence")
                if comparison is not None and key in comparison
            },
        },
        "replay": {
            "working_directory": ".",
            "command": _make_replay_command(options),
            "note": "Run from this directory; baseline.png and current.png are the immutable comparison inputs.",
        },
    }
    try:
        _write_json(manifest_path, manifest)
    except OSError as exc:
        return _emit_error(f"could not write evidence manifest: {exc}")

    if comparison_error is not None:
        print(f"error: {comparison_error['message']}", file=sys.stderr)
    print(json.dumps(report))
    if comparison_error is not None:
        return 2
    return 0 if report["status"] == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    try:
        options = _parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        message = str(exc)
        if message.startswith("error: invalid --ignore-rect:"):
            detail = message.removeprefix("error: ")
            return _emit_error(
                detail,
                structured={
                    "schema_version": 1,
                    "operation": "verify",
                    "status": "error",
                    "error": {"code": "invalid_arguments", "message": detail},
                },
            )
        print(message if message.startswith("loop_compare.sh") or message.startswith("error:") else f"error: {message}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        return int(exc.code or 0)
    return run(options)


if __name__ == "__main__":
    raise SystemExit(main())
