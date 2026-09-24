"""Bounded UI retrieval for Seer.

This module owns the Python side of the native accessibility and OCR worker
protocol. It deliberately returns observations only; assertion policy belongs
to the caller.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SCRIPTS = Path(__file__).resolve().parent
NATIVE_SOURCE = SCRIPTS / "seer_native.swift"


class InspectionError(Exception):
    """A structured failure while retrieving UI state."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise InspectionError("query_timeout", "UI inspection exceeded its timeout")
    return value


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    """Stop a command and any descendants that inherited its output pipes."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:  # pragma: no cover - Seer currently targets macOS
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=0.25)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover
                process.kill()
        except ProcessLookupError:
            pass
        process.communicate()


def _run_process(
    command: Sequence[str],
    deadline: float,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command inside the overall inspection deadline and process group."""
    _remaining(deadline)
    try:
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=(os.name == "posix"),
        )
    except FileNotFoundError as exc:
        raise InspectionError("dependency_missing", f"required command not found: {command[0]}") from exc
    except OSError as exc:
        raise InspectionError("subprocess_failed", f"could not run {command[0]}: {exc}") from exc

    try:
        stdout, stderr = process.communicate(timeout=_remaining(deadline))
    except InspectionError:
        _kill_process_group(process)
        raise
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(process)
        raise InspectionError(
            "query_timeout",
            "UI inspection exceeded its timeout",
            {"command": str(command[0])},
        ) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _output_root() -> Path:
    configured = os.environ.get("SEER_OUT_DIR", os.environ.get("SEER_TMP_DIR"))
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve()
    return (Path.cwd() / ".seer").resolve()


def _new_run_dir(root: Path) -> Path:
    path = root / "inspect" / f"run-{time.time_ns()}-{uuid.uuid4().hex[:10]}"
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise InspectionError("filesystem_error", f"could not prepare inspection output: {exc}") from exc
    return path


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(payload, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise InspectionError("filesystem_error", f"could not write inspection report: {exc}") from exc


def _get_native_binary(deadline: float, output_root: Path) -> Path:
    """Build the Swift worker once per source, host, OS, and Swift toolchain."""
    source = Path(NATIVE_SOURCE)
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        raise InspectionError("dependency_missing", f"native UI worker source is unavailable: {source}") from exc

    xcrun = shutil.which("xcrun")
    if not xcrun:
        raise InspectionError("dependency_missing", "xcrun is required to build the native UI worker")
    version_result = _run_process([xcrun, "swiftc", "--version"], deadline)
    if version_result.returncode != 0:
        raise InspectionError(
            "native_build_failed",
            "could not identify the Swift compiler",
            {"stderr": version_result.stderr[-2000:]},
        )

    source_hash = hashlib.sha256(source_bytes).hexdigest()
    toolchain = (version_result.stdout or version_result.stderr).strip()
    os_version = platform.mac_ver()[0] or platform.release()
    architecture = platform.machine() or "unknown"
    fingerprint_data = "\n".join((source_hash, sys.platform, os_version, architecture, toolchain))
    fingerprint = hashlib.sha256(fingerprint_data.encode("utf-8")).hexdigest()
    safe_arch = "".join(character if character.isalnum() or character in "-_" else "_" for character in architecture)
    cache_dir = output_root / "cache"
    binary = cache_dir / f"native-{source_hash[:16]}-{safe_arch}-{fingerprint[:12]}"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InspectionError("filesystem_error", f"could not prepare native worker cache: {exc}") from exc
    if binary.is_file() and os.access(binary, os.X_OK):
        return binary

    try:
        with tempfile.TemporaryDirectory(prefix=".native-build-", dir=cache_dir) as temporary_dir:
            staged_binary = Path(temporary_dir) / "seer_native"
            compiled = _run_process([xcrun, "swiftc", str(source), "-o", str(staged_binary)], deadline)
            if compiled.returncode != 0 or not staged_binary.is_file():
                raise InspectionError(
                    "native_build_failed",
                    "could not build the native UI worker",
                    {"stderr": compiled.stderr[-4000:]},
                )
            os.chmod(staged_binary, 0o755)
            os.replace(staged_binary, binary)
    except InspectionError:
        raise
    except OSError as exc:
        raise InspectionError("native_build_failed", f"could not publish the native UI worker: {exc}") from exc
    return binary


def _parse_worker_result(result: subprocess.CompletedProcess[str], operation: str) -> dict[str, Any]:
    try:
        document = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InspectionError(
            "invalid_subprocess_output",
            f"native {operation} worker returned invalid JSON",
            {"stderr": result.stderr[-2000:]},
        ) from exc
    if (not isinstance(document, dict) or isinstance(document.get("schema_version"), bool)
            or document.get("schema_version") != 1):
        raise InspectionError("invalid_subprocess_output", f"native {operation} worker returned an invalid document")
    if document.get("status") == "error":
        error = document.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
            raise InspectionError("invalid_subprocess_output", f"native {operation} worker returned an invalid error")
        details = error.get("details")
        return_code = result.returncode
        raise InspectionError(
            error["code"],
            error["message"],
            details if isinstance(details, dict) else {"exit_code": return_code},
        )
    if result.returncode != 0 or document.get("status") != "pass":
        raise InspectionError(
            "subprocess_failed",
            f"native {operation} worker failed",
            {"exit_code": result.returncode, "stderr": result.stderr[-2000:]},
        )
    return document


def _run_native(binary: Path, arguments: Sequence[str], deadline: float) -> dict[str, Any]:
    result = _run_process([str(binary), *arguments], deadline)
    return _parse_worker_result(result, arguments[0] if arguments else "query")


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InspectionError("invalid_subprocess_output", f"native worker returned an invalid {label}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise InspectionError("invalid_subprocess_output", f"native worker returned an invalid {label}") from exc
    if not math.isfinite(number) or number <= 0:
        raise InspectionError("invalid_subprocess_output", f"native worker returned an invalid {label}")
    return number


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InspectionError("invalid_subprocess_output", f"native worker returned an invalid {label}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise InspectionError("invalid_subprocess_output", f"native worker returned an invalid {label}") from exc
    if not math.isfinite(number):
        raise InspectionError("invalid_subprocess_output", f"native worker returned an invalid {label}")
    return number


def _validate_window(document: dict[str, Any], requested_id: int, *, expected_source: str) -> dict[str, Any]:
    if document.get("source") != expected_source:
        raise InspectionError("invalid_subprocess_output", f"native worker returned an unexpected {expected_source} source")
    window = document.get("window")
    if not isinstance(window, dict):
        raise InspectionError("invalid_subprocess_output", "native worker omitted window identity")
    window_id = window.get("window_id")
    if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id != requested_id:
        raise InspectionError("stale_window", "native worker returned a different window ID")
    pid = window.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise InspectionError("invalid_subprocess_output", "native worker returned an invalid window process ID")
    title = window.get("title")
    if not isinstance(title, str):
        raise InspectionError("invalid_subprocess_output", "native worker returned an invalid window title")
    bounds = window.get("bounds")
    if not isinstance(bounds, dict):
        raise InspectionError("invalid_subprocess_output", "native worker returned invalid window bounds")
    validated_bounds = {
        "x": _finite_number(bounds.get("x"), "window x coordinate"),
        "y": _finite_number(bounds.get("y"), "window y coordinate"),
        "width": _positive_number(bounds.get("width"), "window width"),
        "height": _positive_number(bounds.get("height"), "window height"),
    }
    return {
        "window_id": window_id,
        "pid": pid,
        "title": title,
        "bounds": validated_bounds,
    }


def _validate_region(region: Any) -> tuple[float, float, float, float] | None:
    if region is None:
        return None
    if isinstance(region, (str, bytes)) or not isinstance(region, Sequence) or len(region) != 4:
        raise InspectionError("invalid_arguments", "region must contain x, y, width, and height")
    values: list[float] = []
    for label, value in zip(("region x", "region y", "region width", "region height"), region):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InspectionError("invalid_arguments", f"{label} must be a finite number")
        try:
            number = float(value)
        except OverflowError as exc:
            raise InspectionError("invalid_arguments", f"{label} must be a finite number") from exc
        if not math.isfinite(number):
            raise InspectionError("invalid_arguments", f"{label} must be a finite number")
        values.append(number)
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0 or not math.isfinite(x + width) or not math.isfinite(y + height):
        raise InspectionError("invalid_arguments", "region origin must be nonnegative and dimensions must be positive")
    return x, y, width, height


def _check_region_within_window(region: tuple[float, float, float, float] | None, window: dict[str, Any]) -> None:
    if region is None:
        return
    x, y, width, height = region
    bounds = window["bounds"]
    if x + width > bounds["width"] or y + height > bounds["height"]:
        raise InspectionError(
            "invalid_arguments",
            "region must fit within the window's local point bounds",
            {"window_size": {"width": bounds["width"], "height": bounds["height"]}},
        )


def _region_document(region: tuple[float, float, float, float] | None) -> dict[str, float] | None:
    if region is None:
        return None
    x, y, width, height = region
    return {"x": x, "y": y, "width": width, "height": height}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rect(
    value: Any,
    label: str,
    *,
    allow_zero_size: bool = False,
    allow_negative_coordinates: bool = False,
) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InspectionError("invalid_subprocess_output", f"native worker returned invalid {label} bounds")
    x = _finite_number(value.get("x"), f"{label} x coordinate")
    y = _finite_number(value.get("y"), f"{label} y coordinate")
    width = _finite_number(value.get("width"), f"{label} width")
    height = _finite_number(value.get("height"), f"{label} height")
    if ((not allow_negative_coordinates and (x < 0 or y < 0)) or width < 0 or height < 0
            or (not allow_zero_size and (width <= 0 or height <= 0))):
        raise InspectionError("invalid_subprocess_output", f"native worker returned invalid {label} bounds")
    if not math.isfinite(x + width) or not math.isfinite(y + height):
        raise InspectionError("invalid_subprocess_output", f"native worker returned invalid {label} bounds")
    return {"x": x, "y": y, "width": width, "height": height}


_STRUCTURAL_AX_ROLES = {
    "axapplication",
    "axbrowser",
    "axcolumn",
    "axwindow",
    "axgroup",
    "axlist",
    "axoutline",
    "axrow",
    "axscrollarea",
    "axsplitgroup",
    "axsplitter",
    "axtable",
    "axtabgroup",
    "axlayoutarea",
    "axtoolbar",
    "axwebarea",
}


def _ax_element_may_need_scoping(role: str | None) -> bool:
    normalized = role.strip().lower() if role else ""
    return not normalized or normalized not in _STRUCTURAL_AX_ROLES


def _validate_element_id(value: Any, label: str) -> int | str:
    if isinstance(value, bool) or not (isinstance(value, int) or isinstance(value, str)):
        raise InspectionError("invalid_subprocess_output", f"native {label} has an invalid ID")
    if isinstance(value, int) and value <= 0:
        raise InspectionError("invalid_subprocess_output", f"native {label} has an invalid ID")
    if isinstance(value, str) and not value:
        raise InspectionError("invalid_subprocess_output", f"native {label} has an invalid ID")
    return value


def _normalize_ax_elements(
    document: dict[str, Any], region: tuple[float, float, float, float] | None
) -> tuple[list[dict[str, Any]], bool, list[Any]]:
    elements = document.get("elements")
    complete = document.get("complete")
    issues = document.get("issues")
    if not isinstance(elements, list) or not isinstance(complete, bool) or not isinstance(issues, list):
        raise InspectionError("invalid_subprocess_output", "native accessibility worker returned an invalid tree")
    normalized: list[dict[str, Any]] = []
    out_issues = list(issues)
    is_complete = complete
    for index, raw in enumerate(elements):
        if not isinstance(raw, dict):
            raise InspectionError("invalid_subprocess_output", f"native accessibility element {index} is invalid")
        element_id = _validate_element_id(raw.get("id"), f"accessibility element {index}")
        role = raw.get("role")
        name = raw.get("name")
        value = raw.get("value")
        enabled = raw.get("enabled")
        confidence = raw.get("confidence")
        valid_value = value is None or isinstance(value, (str, bool, int, float))
        if isinstance(value, float) and not math.isfinite(value):
            valid_value = False
        if isinstance(value, int) and not isinstance(value, bool):
            try:
                float(value)
            except OverflowError:
                valid_value = False
        if (role is not None and not isinstance(role, str)) or (name is not None and not isinstance(name, str)) or not valid_value:
            raise InspectionError("invalid_subprocess_output", f"native accessibility element {index} has invalid text fields")
        if enabled is not None and not isinstance(enabled, bool):
            raise InspectionError("invalid_subprocess_output", f"native accessibility element {index} has invalid enabled state")
        if confidence is not None:
            confidence = _finite_number(confidence, f"accessibility element {index} confidence")
            if not 0 <= confidence <= 1:
                raise InspectionError("invalid_subprocess_output", f"native accessibility element {index} has invalid confidence")
        bounds = _rect(
            raw.get("bounds"),
            f"accessibility element {index}",
            allow_negative_coordinates=True,
        )
        item = {
            "id": element_id,
            "role": role,
            "name": name,
            "value": value,
            "bounds": bounds,
            "enabled": enabled,
            "confidence": confidence,
        }
        if bounds is None and region is not None and _ax_element_may_need_scoping(role):
            is_complete = False
            out_issues.append({
                "code": "region_scope_unknown",
                "message": "an accessibility element without bounds could not be included or excluded from the requested region",
                "element_index": index,
                "role": role,
            })
            continue
        if region is not None and bounds is not None:
            x, y, width, height = region
            center_x = bounds["x"] + bounds["width"] / 2
            center_y = bounds["y"] + bounds["height"] / 2
            if not (x <= center_x < x + width and y <= center_y < y + height):
                continue
        normalized.append(item)
    return normalized, is_complete, out_issues


def _load_pillow():
    try:
        from PIL import Image
    except ImportError as exc:
        raise InspectionError("dependency_missing", "Pillow is required for OCR image handling") from exc
    return Image


def _capture_exact_window(window_id: int, destination: Path, deadline: float) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SCRIPTS / "seer"),
        "capture",
        "--window-id",
        str(window_id),
        "--out",
        str(destination),
        "--json",
    ]
    result = _run_process(command, deadline)
    try:
        document = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InspectionError(
            "invalid_subprocess_output",
            "Seer capture returned invalid JSON",
            {"stderr": result.stderr[-2000:]},
        ) from exc
    if (not isinstance(document, dict) or isinstance(document.get("schema_version"), bool)
            or document.get("schema_version") != 1):
        raise InspectionError("invalid_subprocess_output", "Seer capture returned an invalid document")
    if result.returncode != 0 or document.get("status") != "pass" or document.get("operation") != "capture":
        error = document.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str) and isinstance(error.get("message"), str):
            details = error.get("details")
            raise InspectionError(error["code"], error["message"], details if isinstance(details, dict) else {})
        raise InspectionError(
            "subprocess_failed",
            "Seer could not capture the requested window",
            {"exit_code": result.returncode, "stderr": result.stderr[-2000:]},
        )

    artifacts = document.get("artifacts")
    capture = document.get("capture")
    if not isinstance(artifacts, dict) or not isinstance(capture, dict):
        raise InspectionError("invalid_subprocess_output", "Seer capture omitted image metadata")
    try:
        current = Path(artifacts["current"]).resolve()
        metadata = Path(artifacts["metadata"]).resolve()
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise InspectionError("invalid_subprocess_output", "Seer capture returned invalid artifact paths") from exc
    if current != destination.resolve() or not current.is_file() or not metadata.is_file():
        raise InspectionError("invalid_subprocess_output", "Seer capture did not create its requested image and metadata")
    if isinstance(capture.get("window_id"), bool) or capture.get("window_id") != window_id:
        raise InspectionError("stale_window", "capture metadata identifies a different window")
    try:
        sidecar = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InspectionError("invalid_subprocess_output", "Seer capture metadata could not be read") from exc
    if not isinstance(sidecar, dict) or isinstance(sidecar.get("window_id"), bool) or sidecar.get("window_id") != window_id or sidecar.get("image_sha256") != capture.get("image_sha256"):
        raise InspectionError("invalid_subprocess_output", "Seer capture metadata does not match its report")
    capture_report = destination.parent / "capture_report.json"
    _atomic_json(capture_report, document)
    return {"current": current, "metadata": metadata, "capture_report": capture_report, "capture": capture}


def _crop_image(
    image_path: Path,
    crop_path: Path,
    logical_size: tuple[float, float],
    region: tuple[float, float, float, float] | None,
    deadline: float,
) -> dict[str, Any]:
    Image = _load_pillow()
    try:
        with Image.open(image_path) as image:
            if image.format != "PNG":
                raise InspectionError("invalid_subprocess_output", "Seer capture is not a PNG image")
            image.load()
            image_width, image_height = image.size
            if image_width <= 0 or image_height <= 0:
                raise InspectionError("invalid_subprocess_output", "Seer capture has invalid pixel dimensions")
            logical_width, logical_height = logical_size
            scale_x = image_width / logical_width
            scale_y = image_height / logical_height
            if not math.isfinite(scale_x) or not math.isfinite(scale_y) or scale_x <= 0 or scale_y <= 0:
                raise InspectionError("capture_geometry_mismatch", "capture pixel scale is invalid")
            if not math.isclose(scale_x, scale_y, rel_tol=1e-4, abs_tol=1e-6):
                raise InspectionError(
                    "capture_geometry_mismatch",
                    "capture pixels do not map uniformly to the window's logical points",
                    {"scale_x": scale_x, "scale_y": scale_y},
                )
            crop_origin_x = crop_origin_y = 0
            if region is None:
                image_to_inspect = image.copy()
            else:
                x, y, width, height = region
                crop_origin_x = max(0, math.floor(x * scale_x))
                crop_origin_y = max(0, math.floor(y * scale_y))
                right = min(image_width, math.ceil((x + width) * scale_x))
                bottom = min(image_height, math.ceil((y + height) * scale_y))
                if right <= crop_origin_x or bottom <= crop_origin_y:
                    raise InspectionError("invalid_arguments", "region maps to an empty pixel crop")
                image_to_inspect = image.crop((crop_origin_x, crop_origin_y, right, bottom))
                image_to_inspect.save(crop_path, format="PNG")
            _remaining(deadline)
    except InspectionError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise InspectionError("invalid_subprocess_output", f"could not decode Seer capture: {exc}") from exc

    return {
        "image_size": {"width": image_width, "height": image_height},
        "scale_x": scale_x,
        "scale_y": scale_y,
        "ocr_image": crop_path if region is not None else image_path,
        "ocr_image_size": {"width": image_to_inspect.width, "height": image_to_inspect.height},
        "crop_origin": {"x": crop_origin_x, "y": crop_origin_y},
    }


def _normalize_ocr_elements(
    document: dict[str, Any],
    mapping: dict[str, Any],
) -> list[dict[str, Any]]:
    image_size = document.get("image_size")
    if not isinstance(image_size, dict):
        raise InspectionError("invalid_subprocess_output", "native OCR worker omitted image dimensions")
    image_width = image_size.get("width")
    image_height = image_size.get("height")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (image_width, image_height)):
        raise InspectionError("invalid_subprocess_output", "native OCR worker returned invalid image dimensions")
    if image_width != mapping["ocr_image_size"]["width"] or image_height != mapping["ocr_image_size"]["height"]:
        raise InspectionError("invalid_subprocess_output", "native OCR image dimensions do not match the inspected capture")
    elements = document.get("elements")
    if not isinstance(elements, list):
        raise InspectionError("invalid_subprocess_output", "native OCR worker returned an invalid element list")
    scale_x = mapping["scale_x"]
    scale_y = mapping["scale_y"]
    origin = mapping["crop_origin"]
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(elements):
        if not isinstance(raw, dict) or raw.get("role") != "text":
            raise InspectionError("invalid_subprocess_output", f"native OCR element {index} is invalid")
        element_id = _validate_element_id(raw.get("id"), f"OCR element {index}")
        text = raw.get("value")
        if not isinstance(text, str):
            raise InspectionError("invalid_subprocess_output", f"native OCR element {index} has invalid text")
        name = raw.get("name")
        if name is not None and not isinstance(name, str):
            raise InspectionError("invalid_subprocess_output", f"native OCR element {index} has invalid name")
        pixel_bounds = _rect(raw.get("bounds"), f"OCR element {index}")
        if pixel_bounds is None:
            raise InspectionError("invalid_subprocess_output", f"native OCR element {index} omitted bounds")
        if pixel_bounds["x"] + pixel_bounds["width"] > image_width or pixel_bounds["y"] + pixel_bounds["height"] > image_height:
            raise InspectionError("invalid_subprocess_output", f"native OCR element {index} extends beyond its image")
        confidence = raw.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise InspectionError("invalid_subprocess_output", f"native OCR element {index} has invalid confidence")
        confidence = _finite_number(confidence, f"OCR element {index} confidence")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise InspectionError("invalid_subprocess_output", f"native OCR element {index} has invalid confidence")
        bounds = {
            "x": (origin["x"] + pixel_bounds["x"]) / scale_x,
            "y": (origin["y"] + pixel_bounds["y"]) / scale_y,
            "width": pixel_bounds["width"] / scale_x,
            "height": pixel_bounds["height"] / scale_y,
        }
        normalized.append({
            "id": element_id,
            "role": "text",
            "name": name,
            "value": text,
            "bounds": bounds,
            "enabled": None,
            "confidence": confidence,
        })
    return normalized


def _same_window(before: dict[str, Any], after: dict[str, Any]) -> None:
    if before["window_id"] != after["window_id"] or before["pid"] != after["pid"]:
        raise InspectionError(
            "stale_window",
            "window identity changed during UI inspection",
            {"before": before, "after": after},
        )
    for key in ("x", "y", "width", "height"):
        if not math.isclose(before["bounds"][key], after["bounds"][key], rel_tol=0.0, abs_tol=1e-3):
            raise InspectionError(
                "window_changed",
                "window bounds changed during UI inspection",
                {"before": before, "after": after},
            )


def inspect_ui(
    window_id: int,
    *,
    source: str = "ax",
    region: tuple[float, float, float, float] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Retrieve accessibility or OCR observations from one exact visible window."""
    started = time.monotonic()
    if isinstance(window_id, bool) or not isinstance(window_id, int) or not 1 <= window_id <= 0xFFFFFFFF:
        raise InspectionError("invalid_arguments", "window ID must be between 1 and 4294967295")
    if source not in ("ax", "ocr"):
        raise InspectionError("invalid_arguments", "source must be 'ax' or 'ocr'")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise InspectionError("invalid_arguments", "timeout must be a positive finite number of seconds")
    try:
        timeout_value = float(timeout)
    except OverflowError as exc:
        raise InspectionError("invalid_arguments", "timeout must be a positive finite number of seconds") from exc
    if not math.isfinite(timeout_value) or timeout_value <= 0:
        raise InspectionError("invalid_arguments", "timeout must be a positive finite number of seconds")
    deadline = started + timeout_value
    normalized_region = _validate_region(region)
    output_root = _output_root()
    run_dir = _new_run_dir(output_root)
    report_path = run_dir / "report.json"
    artifacts: dict[str, Any] = {
        "current": None,
        "crop": None,
        "metadata": None,
        "capture_report": None,
        "report": str(report_path),
    }
    window: dict[str, Any] | None = None

    try:
        binary = _get_native_binary(deadline, output_root)
        if source == "ax":
            accessibility = _run_native(binary, ["ax", "--window-id", str(window_id)], deadline)
            if accessibility.get("source") != "accessibility":
                raise InspectionError("invalid_subprocess_output", "native accessibility worker returned an unexpected source")
            window = _validate_window(accessibility, window_id, expected_source="accessibility")
            _check_region_within_window(normalized_region, window)
            elements, complete, issues = _normalize_ax_elements(accessibility, normalized_region)
            after_doc = _run_native(binary, ["window", "--window-id", str(window_id)], deadline)
            after_window = _validate_window(after_doc, window_id, expected_source="window")
            _same_window(window, after_window)
            payload = {
                "schema_version": 1,
                "operation": "inspect",
                "status": "pass",
                "source": "accessibility",
                "window_id": window_id,
                "window": window,
                "region": _region_document(normalized_region),
                "coordinate_space": "window_points",
                "complete": complete,
                "issues": issues,
                "elements": elements,
                "artifacts": artifacts,
            }
            if normalized_region is not None:
                payload["region_semantics"] = "Accessibility elements are included when their bounds center lies inside the region; elements with missing bounds cannot be scoped."
        else:
            before_doc = _run_native(binary, ["window", "--window-id", str(window_id)], deadline)
            window = _validate_window(before_doc, window_id, expected_source="window")
            _check_region_within_window(normalized_region, window)
            Image = _load_pillow()
            _ = Image
            image_path = run_dir / "current.png"
            capture = _capture_exact_window(window_id, image_path, deadline)
            artifacts.update({
                "current": str(capture["current"]),
                "metadata": str(capture["metadata"]),
                "capture_report": str(capture["capture_report"]),
            })
            mapping = _crop_image(
                Path(capture["current"]),
                run_dir / "crop.png",
                (window["bounds"]["width"], window["bounds"]["height"]),
                normalized_region,
                deadline,
            )
            if normalized_region is not None:
                artifacts["crop"] = str(mapping["ocr_image"])
            ocr = _run_native(binary, ["ocr", "--image", str(mapping["ocr_image"])], deadline)
            if ocr.get("source") != "ocr":
                raise InspectionError("invalid_subprocess_output", "native OCR worker returned an unexpected source")
            if "complete" not in ocr or not isinstance(ocr["complete"], bool):
                raise InspectionError("invalid_subprocess_output", "native OCR worker returned invalid completion state")
            complete = ocr["complete"]
            issues = ocr.get("issues", [])
            if not isinstance(issues, list):
                raise InspectionError("invalid_subprocess_output", "native OCR worker returned invalid issues")
            elements = _normalize_ocr_elements(ocr, mapping)
            after_doc = _run_native(binary, ["window", "--window-id", str(window_id)], deadline)
            after_window = _validate_window(after_doc, window_id, expected_source="window")
            _same_window(window, after_window)
            payload = {
                "schema_version": 1,
                "operation": "inspect",
                "status": "pass",
                "source": "ocr",
                "window_id": window_id,
                "window": window,
                "region": _region_document(normalized_region),
                "coordinate_space": "window_points",
                "complete": complete,
                "issues": issues,
                "elements": elements,
                "artifacts": artifacts,
            }
            if normalized_region is not None:
                payload["region_semantics"] = "OCR ran on a crop mapped from window points to capture pixels with pixel edges rounded outward, then mapped detections back to window points."

        _remaining(deadline)
        payload["observed_at"] = _utc_now()
        payload["elapsed_seconds"] = max(0.0, time.monotonic() - started)
        _atomic_json(report_path, payload)
        _remaining(deadline)
        return payload
    except InspectionError as exc:
        exc.details.setdefault("artifacts", dict(artifacts))
        exc.details.setdefault("report", str(report_path))
        error_report = {
            "schema_version": 1,
            "operation": "inspect",
            "status": "error",
            "source": "accessibility" if source == "ax" else "ocr",
            "window_id": window_id,
            "window": window,
            "region": _region_document(normalized_region),
            "coordinate_space": "window_points",
            "complete": False,
            "issues": [],
            "elements": [],
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "observed_at": _utc_now(),
            "elapsed_seconds": max(0.0, time.monotonic() - started),
            "artifacts": artifacts,
        }
        try:
            _atomic_json(report_path, error_report)
        except InspectionError:
            pass
        raise
