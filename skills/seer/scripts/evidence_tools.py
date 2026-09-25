#!/usr/bin/env python3
"""Safe summaries and explicit exports for Seer evidence."""

from __future__ import annotations

import hashlib
import html
import importlib.metadata
import importlib.util
import errno
import json
import math
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from seer_version import SEER_VERSION


_RESULT_STATUSES = {"pass", "fail", "error", "needs_baseline"}
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_EXPORT_BYTES = 128 * 1024 * 1024
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MARKDOWN_PUNCTUATION = "\\`*_{}[]()#+-.!|"


class EvidenceError(Exception):
    """An expected, machine-readable evidence operation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _json_bytes(raw: bytes, description: str) -> Dict[str, Any]:
    if len(raw) > _MAX_JSON_BYTES:
        raise EvidenceError("invalid_evidence", f"{description} is too large")
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_nonstandard_constant(_value):
        raise ValueError("nonstandard JSON constant")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys,
                           parse_constant=reject_nonstandard_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise EvidenceError("invalid_evidence", f"{description} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise EvidenceError("invalid_evidence", f"{description} must contain a JSON object")
    return value


def _read_result(path: Path) -> Dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError as exc:
        raise EvidenceError("filesystem_error", "the input file was not found") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise EvidenceError("unsafe_path", "the input file must not be a symlink") from exc
        raise EvidenceError("filesystem_error", "the input file could not be opened") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise EvidenceError("invalid_arguments", "the input must be a regular result JSON file")
        with os.fdopen(fd, "rb") as source:
            raw = source.read(_MAX_JSON_BYTES + 1)
        if len(raw) > _MAX_JSON_BYTES:
            raise EvidenceError("invalid_evidence", "the input file is too large")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    result = _json_bytes(raw, "the input file")
    _validate_source_result(result)
    if "files" in result and "run_id" in result:
        raise EvidenceError("invalid_evidence", "pass a comparison bundle directory instead of its manifest file")
    return result


def _safe_relative_path(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise EvidenceError("unsafe_path", "the evidence manifest contains an unsafe file path")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise EvidenceError("unsafe_path", "the evidence manifest contains an unsafe file path")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise EvidenceError("unsafe_path", "the evidence manifest contains an absolute file path")
    parts = tuple(value.split("/"))
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        raise EvidenceError("unsafe_path", "the evidence manifest contains an unsafe file path")
    if "/".join(parts) != value:
        raise EvidenceError("unsafe_path", "the evidence manifest contains a non-canonical file path")
    return parts


def _open_bundle_file(bundle: Path, relative_parts: Tuple[str, ...]):
    """Open a regular file below bundle without following any symlink."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    supports_dir_fd = os.open in getattr(os, "supports_dir_fd", set())
    if supports_dir_fd and nofollow and directory:
        parent_fd = None
        try:
            parent_fd = os.open(str(bundle), os.O_RDONLY | directory)
            for part in relative_parts:
                part_stat = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(part_stat.st_mode):
                    raise EvidenceError("unsafe_path", "an evidence path must not follow symlinks")
                if part != relative_parts[-1] and not stat.S_ISDIR(part_stat.st_mode):
                    raise EvidenceError("unsafe_path", "an evidence path contains a non-directory parent")
                if part == relative_parts[-1] and not stat.S_ISREG(part_stat.st_mode):
                    raise EvidenceError("unsafe_path", "an evidence path does not name a regular file")
                if part == relative_parts[-1]:
                    break
                next_fd = os.open(part, os.O_RDONLY | directory | nofollow, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            file_fd = os.open(relative_parts[-1], os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
                              dir_fd=parent_fd)
        except EvidenceError:
            raise
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise EvidenceError("unsafe_path", "an evidence path must not follow symlinks") from exc
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                raise EvidenceError("evidence_tampered", "a manifest-declared evidence file is missing") from exc
            raise EvidenceError("filesystem_error", "a manifest-declared evidence file could not be opened") from exc
        finally:
            if parent_fd is not None:
                os.close(parent_fd)
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            os.close(file_fd)
            raise EvidenceError("unsafe_path", "an evidence path does not name a regular file")
        return os.fdopen(file_fd, "rb")

    candidate = bundle
    for part in relative_parts:
        candidate = candidate / part
        try:
            item_stat = candidate.lstat()
        except FileNotFoundError as exc:
            raise EvidenceError("evidence_tampered", "a manifest-declared evidence file is missing") from exc
        if stat.S_ISLNK(item_stat.st_mode):
            raise EvidenceError("unsafe_path", "an evidence path must not follow symlinks")
    if not stat.S_ISREG(item_stat.st_mode):
        raise EvidenceError("unsafe_path", "an evidence path does not name a regular file")
    try:
        return candidate.open("rb")
    except OSError as exc:
        raise EvidenceError("filesystem_error", "a manifest-declared evidence file could not be read") from exc


def _read_bundle_file(bundle: Path, parts: Tuple[str, ...], expected_size: Optional[int] = None) -> bytes:
    with _open_bundle_file(bundle, parts) as source:
        data = source.read(_MAX_JSON_BYTES + 1 if expected_size is None else expected_size + 1)
        if expected_size is not None and len(data) != expected_size:
            raise EvidenceError("evidence_tampered", "a manifest-declared evidence file has the wrong size")
        return data


def _manifest_entries(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    schema = manifest.get("schema_version")
    if type(schema) is not int:
        raise EvidenceError("invalid_evidence", "the comparison manifest is missing a valid schema_version")
    if schema != 1:
        raise EvidenceError("unsupported_schema", "the comparison manifest uses an unsupported schema version")
    files = manifest.get("files")
    paths = manifest.get("paths")
    if not isinstance(files, dict) or not files or not isinstance(paths, dict):
        raise EvidenceError("invalid_evidence", "the comparison manifest is missing its file declarations")

    entries: Dict[str, Dict[str, Any]] = {}
    seen_paths = set()
    for role, item in files.items():
        if not isinstance(role, str) or not _ROLE_RE.fullmatch(role) or not isinstance(item, dict):
            raise EvidenceError("invalid_evidence", "the comparison manifest has an invalid file declaration")
        relative = _safe_relative_path(item.get("path"))
        archive_path = "/".join(relative)
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if archive_path == "manifest.json" or archive_path.startswith("manifest.json/"):
            raise EvidenceError("unsafe_path", "a declared evidence path collides with manifest.json")
        if archive_path in seen_paths:
            raise EvidenceError("unsafe_path", "the comparison manifest declares the same path more than once")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise EvidenceError("invalid_evidence", "the comparison manifest has an invalid SHA-256 value")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise EvidenceError("invalid_evidence", "the comparison manifest has an invalid file size")
        if paths.get(role) != item.get("path"):
            raise EvidenceError("evidence_tampered", "the comparison manifest file path declarations disagree")
        seen_paths.add(archive_path)
        entries[role] = {
            "parts": relative,
            "path": archive_path,
            "sha256": digest,
            "size_bytes": size,
        }
    if set(paths) != set(entries):
        raise EvidenceError("evidence_tampered", "the comparison manifest file path declarations disagree")
    if "report" not in entries or "baseline" not in entries or "current" not in entries:
        raise EvidenceError("invalid_evidence", "the comparison manifest is missing required report or image files")
    if entries["report"]["size_bytes"] > _MAX_JSON_BYTES:
        raise EvidenceError("invalid_evidence", "the bundle report is too large")

    images = manifest.get("images")
    if not isinstance(images, dict):
        raise EvidenceError("invalid_evidence", "the comparison manifest is missing image metadata")
    for name in ("baseline", "current"):
        image = images.get(name)
        entry = entries[name]
        if (not isinstance(image, dict) or image.get("path") != entry["path"]
                or image.get("sha256") != entry["sha256"]):
            raise EvidenceError("evidence_tampered", "the comparison manifest image declarations disagree")
    for entry in entries.values():
        parts = entry["parts"]
        if any("/".join(parts[:index]) in seen_paths for index in range(1, len(parts))):
            raise EvidenceError("unsafe_path", "the comparison manifest contains colliding file paths")
    return entries


def _verify_entry(bundle: Path, entry: Dict[str, Any], *, collect: bool = False) -> Optional[bytes]:
    digest = hashlib.sha256()
    total = 0
    chunks: List[bytes] = []
    try:
        source = _open_bundle_file(bundle, entry["parts"])
        with source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > entry["size_bytes"]:
                    raise EvidenceError("evidence_tampered", "a manifest-declared evidence file has the wrong size")
                digest.update(chunk)
                if collect:
                    chunks.append(chunk)
    except FileNotFoundError as exc:
        raise EvidenceError("evidence_tampered", "a manifest-declared evidence file is missing") from exc
    except OSError as exc:
        raise EvidenceError("filesystem_error", "a manifest-declared evidence file could not be read") from exc
    if total != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
        raise EvidenceError("evidence_tampered", "a manifest-declared evidence file failed its size or SHA-256 check")
    return b"".join(chunks) if collect else None


def _read_bundle(bundle_path: Path, collect_files: bool = False) -> Tuple[
        Path, Dict[str, Any], bytes, Dict[str, Dict[str, Any]], Dict[str, bytes], Dict[str, Any]]:
    try:
        bundle = bundle_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise EvidenceError("filesystem_error", "the comparison bundle directory was not found") from exc
    except OSError as exc:
        raise EvidenceError("filesystem_error", "the comparison bundle directory could not be opened") from exc
    if not bundle.is_dir():
        raise EvidenceError("invalid_arguments", "the input must be a result JSON file or comparison bundle directory")
    try:
        manifest_bytes = _read_bundle_file(bundle, ("manifest.json",))
    except EvidenceError as exc:
        if exc.code == "evidence_tampered":
            raise EvidenceError("invalid_evidence", "the comparison bundle has no readable manifest.json") from exc
        raise
    manifest = _json_bytes(manifest_bytes, "manifest.json")
    entries = _manifest_entries(manifest)
    if collect_files and len(manifest_bytes) + sum(item["size_bytes"] for item in entries.values()) > _MAX_EXPORT_BYTES:
        raise EvidenceError("evidence_too_large", "ZIP export supports at most 128 MiB of declared evidence; summarize without --export or use a smaller bundle")
    contents: Dict[str, bytes] = {}
    for role, entry in entries.items():
        content = _verify_entry(bundle, entry, collect=collect_files or role == "report")
        if content is not None:
            contents[role] = content
    report_raw = contents["report"]
    report = _json_bytes(report_raw, "the bundle report")
    _validate_source_result(report)
    _validate_bundle_verdict(manifest, report)
    return bundle, manifest, manifest_bytes, entries, contents, report


def _validate_source_result(result: Dict[str, Any]) -> None:
    schema = result.get("schema_version")
    if type(schema) is not int:
        raise EvidenceError("invalid_evidence", "the result is missing a valid schema_version")
    if schema != 1:
        raise EvidenceError("unsupported_schema", "the result uses an unsupported schema version")
    if not isinstance(result.get("status"), str) or result["status"] not in _RESULT_STATUSES:
        raise EvidenceError("invalid_evidence", "the result is missing a supported result status")
    if "operation" not in result:
        raise EvidenceError("invalid_evidence", "the result is missing its operation")
    if result["operation"] is not None and not isinstance(result["operation"], str):
        raise EvidenceError("invalid_evidence", "the result operation must be a string")
    if result["status"] == "error":
        error = result.get("error")
        if (not isinstance(error, dict) or not isinstance(error.get("code"), str)
                or not error.get("code") or not isinstance(error.get("message"), str)
                or not error.get("message")):
            raise EvidenceError("invalid_evidence", "an error result must include its error code and message")
    elif "error" in result:
        raise EvidenceError("invalid_evidence", "a non-error result cannot include an error object")


def _validate_bundle_verdict(manifest: Dict[str, Any], report: Dict[str, Any]) -> None:
    manifest_status = manifest.get("status")
    manifest_operation = manifest.get("operation")
    comparison = manifest.get("comparison")
    if (not isinstance(manifest_status, str) or manifest_status not in _RESULT_STATUSES
            or not isinstance(manifest_operation, str)
            or not isinstance(comparison, dict)):
        raise EvidenceError("invalid_evidence", "the comparison manifest has invalid verdict metadata")
    if report["status"] != manifest_status or comparison.get("status") != manifest_status:
        raise EvidenceError("evidence_tampered", "the comparison manifest and report verdicts disagree")
    # The existing verifier reports a failed baseline-create attempt as a verify error.
    operation_matches = report["operation"] == manifest_operation or (
        manifest_operation == "baseline_create" and manifest_status == "error"
        and report["operation"] == "verify"
    )
    if not operation_matches:
        raise EvidenceError("evidence_tampered", "the comparison manifest and report operations disagree")
    comparison_error = comparison.get("error")
    if manifest_status == "error":
        if comparison_error != report.get("error"):
            raise EvidenceError("evidence_tampered", "the comparison manifest and report errors disagree")
    elif comparison_error is not None:
        raise EvidenceError("evidence_tampered", "a non-error manifest cannot include a comparison error")


def _markdown_text(value: Any) -> str:
    text = html.escape(str(value), quote=True)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    for character in _MARKDOWN_PUNCTUATION:
        text = text.replace(character, "\\" + character)
    return text


def _number(value: Any) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return format(value, ".6g") if isinstance(value, float) else str(value)


def _summary_markdown(result: Dict[str, Any]) -> bytes:
    status = result["status"]
    operation = result.get("operation") or "unknown"
    lines = [
        "# Seer report",
        "",
        f"- Source verdict: {_markdown_text(status)}",
        f"- Operation: {_markdown_text(operation)}",
    ]
    if isinstance(result.get("source"), str):
        lines.append(f"- Evidence source: {_markdown_text(result['source'])}")
    if isinstance(result.get("reason"), str):
        lines.append(f"- Reason: {_markdown_text(result['reason'])}")

    condition = result.get("condition")
    if isinstance(condition, dict):
        for key, label in (
            ("text", "Text condition"),
            ("text_absent", "Absent-text condition"),
            ("enabled", "Enabled control"),
            ("disabled", "Disabled control"),
            ("role", "Control role"),
            ("match", "Match mode"),
        ):
            value = condition.get(key)
            if isinstance(value, str):
                lines.append(f"- {label}: {_markdown_text(value)}")
        confidence = _number(condition.get("min_confidence"))
        if confidence is not None:
            lines.append(f"- Minimum confidence: {confidence}")

    for label, value in (
        ("Changed pixels", result.get("pixels_changed")),
        ("Total pixels", result.get("pixels_total")),
        ("Changed percentage", result.get("percent_changed")),
        ("Average difference percentage", result.get("avg_diff_percent")),
    ):
        formatted = _number(value)
        if formatted is not None:
            suffix = "%" if label.endswith("percentage") else ""
            lines.append(f"- {label}: {formatted}{suffix}")

    threshold = result.get("thresholds")
    has_threshold = False
    if isinstance(threshold, dict):
        formatted = _number(threshold.get("max_diff_percent"))
        if formatted is not None:
            lines.append(f"- Maximum changed percentage: {formatted}%")
            has_threshold = True

    options = result.get("comparison_options")
    if isinstance(options, dict):
        resize = options.get("resize")
        if isinstance(resize, bool):
            lines.append(f"- Resize enabled: {'yes' if resize else 'no'}")
        if not has_threshold:
            formatted = _number(options.get("max_diff_percent"))
            if formatted is not None:
                lines.append(f"- Maximum changed percentage: {formatted}%")
        ignore_rects = options.get("ignore_rects")
        if isinstance(ignore_rects, list):
            lines.append(f"- Ignored regions: {len(ignore_rects)}")

    size = result.get("size")
    if isinstance(size, dict):
        width = _number(size.get("width"))
        height = _number(size.get("height"))
        if width is not None and height is not None:
            lines.append(f"- Image size: {width} × {height}")

    error = result.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        lines.append(f"- Error code: {_markdown_text(error['code'])}")
        if isinstance(error.get("message"), str):
            lines.append(f"- Error: {_markdown_text(error['message'])}")

    for label, value in (("Samples", result.get("samples")),
                         ("Elapsed seconds", result.get("elapsed_seconds"))):
        formatted = _number(value)
        if formatted is not None:
            lines.append(f"- {label}: {formatted}")

    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        references = [(key, value) for key, value in artifacts.items()
                      if isinstance(key, str) and isinstance(value, str)]
        if references:
            lines.extend(("", "## Artifact references"))
            for key, value in references:
                lines.append(f"- {_markdown_text(key)}: {_markdown_text(value)}")

    try:
        return ("\n".join(lines) + "\n").encode("utf-8")
    except UnicodeError as exc:
        raise EvidenceError("invalid_evidence", "the result contains text that cannot be encoded as UTF-8") from exc


def _destination_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.name or path.name in (".", ".."):
        raise EvidenceError("invalid_arguments", "output paths must name a file")
    return path.absolute()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=True))
        return True
    except (ValueError, OSError):
        return False


def _ensure_destinations(out_path: Path, export_path: Optional[Path], bundle: Optional[Path]) -> None:
    if export_path is not None and out_path.resolve(strict=False) == export_path.resolve(strict=False):
        raise EvidenceError("invalid_arguments", "report and ZIP outputs must use different paths")
    if bundle is not None:
        for path in (out_path, export_path):
            if path is not None and _is_within(path, bundle):
                raise EvidenceError("invalid_arguments", "outputs must be outside the source comparison bundle")


def _write_temp(path: Path, data: bytes) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temp = Path(name)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            try:
                temp.unlink()
            except OSError:
                pass
            raise
        return temp
    except OSError as exc:
        raise EvidenceError("filesystem_error", "an output file could not be prepared") from exc


def _write_zip_temp(path: Path, manifest_bytes: bytes, entries: Dict[str, Dict[str, Any]],
                    contents: Dict[str, bytes]) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        os.close(fd)
    except OSError as exc:
        raise EvidenceError("filesystem_error", "the ZIP output could not be prepared") from exc
    temp = Path(name)
    try:
        with zipfile.ZipFile(str(temp), "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _zip_bytes(archive, "manifest.json", manifest_bytes)
            for role, entry in entries.items():
                _zip_bytes(archive, entry["path"], contents[role])
        with temp.open("rb+") as archive_file:
            os.fsync(archive_file.fileno())
        return temp
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _zip_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    archive.writestr(_zip_info(name), data)


def _publish_temp(temp: Path, destination: Path) -> None:
    try:
        os.link(str(temp), str(destination))
    except FileExistsError as exc:
        raise EvidenceError("output_exists", "an output file already exists; choose a new path") from exc
    except OSError as exc:
        raise EvidenceError("filesystem_error", "an output file could not be written") from exc


def _unlink_if_same_file(path: Path, temp: Path) -> None:
    try:
        current = path.stat()
        original = temp.stat()
        if current.st_dev == original.st_dev and current.st_ino == original.st_ino:
            path.unlink()
    except OSError:
        pass


def create_report(input_path: str, output_path: str, export_path: Optional[str] = None) -> Dict[str, Any]:
    """Create a Markdown summary and optionally a verified bundle ZIP."""
    source_path = Path(input_path).expanduser()
    out_path = _destination_path(output_path)
    zip_path = _destination_path(export_path) if export_path is not None else None

    bundle: Optional[Path] = None
    manifest_bytes: Optional[bytes] = None
    entries: Optional[Dict[str, Dict[str, Any]]] = None
    if source_path.is_dir():
        bundle, manifest, manifest_bytes, entries, contents, result = _read_bundle(
            source_path, collect_files=zip_path is not None
        )
    else:
        if zip_path is not None:
            raise EvidenceError("invalid_arguments", "--export requires a comparison bundle directory")
        result = _read_result(source_path)
        _validate_source_result(result)
        contents = {}
    _ensure_destinations(out_path, zip_path, bundle)

    report_bytes = _summary_markdown(result)
    report_temp: Optional[Path] = None
    zip_temp: Optional[Path] = None
    report_published = False
    export_published = False
    try:
        report_temp = _write_temp(out_path, report_bytes)
        if zip_path is not None and bundle is not None and manifest_bytes is not None and entries is not None:
            zip_temp = _write_zip_temp(zip_path, manifest_bytes, entries, contents)
        _publish_temp(report_temp, out_path)
        report_published = True
        if zip_temp is not None and zip_path is not None:
            _publish_temp(zip_temp, zip_path)
            export_published = True
    except BaseException:
        if report_published and report_temp is not None:
            _unlink_if_same_file(out_path, report_temp)
        if export_published and zip_temp is not None and zip_path is not None:
            _unlink_if_same_file(zip_path, zip_temp)
        raise
    finally:
        for temp in (report_temp, zip_temp):
            if temp is not None:
                try:
                    temp.unlink()
                except OSError:
                    pass

    response: Dict[str, Any] = {
        "source_status": result["status"],
        "source_operation": result.get("operation"),
        "artifacts": {"report": str(out_path.resolve(strict=False))},
    }
    if zip_path is not None:
        response["artifacts"]["export"] = str(zip_path.resolve(strict=False))
    return response


def _module_available(module_name: str, distribution: str) -> Dict[str, Any]:
    try:
        available = importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        available = False
    version = None
    if available:
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            pass
    return {"available": available, "version": version}


def diagnostics_payload() -> Dict[str, Any]:
    """Return a privacy-limited inventory without running Seer's doctor probes."""
    macos_release = platform.mac_ver()[0] if sys.platform == "darwin" else None
    return {
        "schema_version": 1,
        "operation": "diagnostics",
        "status": "pass",
        "seer_version": SEER_VERSION,
        "diagnostics": {
            "platform": {
                "value": sys.platform,
                "macos_release": macos_release or None,
                "architecture": platform.machine() or None,
            },
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
            },
            "dependencies": {
                "pillow": _module_available("PIL", "Pillow"),
                "mcp": _module_available("mcp", "mcp"),
                "jsonschema": _module_available("jsonschema", "jsonschema"),
            },
            "tools": {
                "ffmpeg": {"available": shutil.which("ffmpeg") is not None},
                "ffprobe": {"available": shutil.which("ffprobe") is not None},
            },
        },
    }
