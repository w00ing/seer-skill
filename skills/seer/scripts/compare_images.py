#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import sys
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from PIL import Image, ImageChops, ImageDraw


Rect = Tuple[int, int, int, int]
ImageSource = Union[Image.Image, str, os.PathLike]


class ComparisonError(Exception):
    """An expected comparison failure with a machine-readable error code."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def load_image(path: Union[str, os.PathLike]) -> Image.Image:
    """Load an image while keeping the source format and metadata on its copy."""
    with Image.open(path) as opened:
        image_format = opened.format
        metadata = opened.info.copy()
        image = opened.convert("RGBA")
        image.info.update(metadata)
        image.format = image_format
        return image


def percentage(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number between 0 and 100") from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError("must be a finite number between 0 and 100")
    return parsed


def parse_rect(value: str) -> Rect:
    """Parse an X,Y,WIDTH,HEIGHT rectangle with nonnegative integer coordinates."""
    parts = value.split(",")
    if len(parts) != 4 or any(re.fullmatch(r"\s*\d+\s*", part) is None for part in parts):
        raise ComparisonError(
            "invalid_arguments",
            "expected X,Y,WIDTH,HEIGHT using nonnegative integers",
            {"value": value},
        )
    x, y, width, height = (int(part.strip()) for part in parts)
    if width <= 0 or height <= 0:
        raise ComparisonError(
            "invalid_arguments",
            "rectangle width and height must be positive",
            {"value": value},
        )
    return (x, y, width, height)


def _coerce_image(source: ImageSource, label: str) -> Image.Image:
    if isinstance(source, Image.Image):
        original = source
        image_format = getattr(source, "format", None)
        metadata = source.info.copy()
    elif isinstance(source, (str, os.PathLike)):
        path = os.fspath(source)
        if not os.path.isfile(path):
            raise ComparisonError(
                "image_not_found",
                f"{label} image not found: {path}",
                {"image": label, "path": os.path.abspath(path)},
            )
        try:
            with Image.open(path) as opened:
                image_format = opened.format
                metadata = opened.info.copy()
                original = opened.copy()
        except (OSError, ValueError) as exc:
            raise ComparisonError(
                "invalid_image",
                f"could not read {label} image: {exc}",
                {"image": label, "path": os.path.abspath(path)},
            ) from exc
    else:
        raise ComparisonError(
            "invalid_arguments",
            f"{label} must be an image or a filesystem path",
            {"image": label, "value_type": type(source).__name__},
        )

    try:
        image = original.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise ComparisonError(
            "invalid_image", f"could not decode {label} image: {exc}", {"image": label}
        ) from exc
    image.info.update(metadata)
    image.format = image_format
    return image


def _size_evidence(image: Image.Image) -> Dict[str, int]:
    return {"width": image.width, "height": image.height}


def _dpi_evidence(image: Image.Image) -> Optional[Dict[str, float]]:
    value = image.info.get("dpi")
    try:
        if isinstance(value, (int, float)):
            x_dpi = y_dpi = float(value)
        elif isinstance(value, (tuple, list)) and len(value) >= 2:
            x_dpi, y_dpi = float(value[0]), float(value[1])
        else:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(axis) and axis > 0 for axis in (x_dpi, y_dpi)):
        return None
    return {"x": x_dpi, "y": y_dpi}


def _same_dpi(first: Dict[str, float], second: Dict[str, float]) -> bool:
    return all(
        math.isclose(first[axis], second[axis], rel_tol=0.001, abs_tol=0.1)
        for axis in ("x", "y")
    )


def _validate_rectangles(rectangles: Sequence[Rect], width: int, height: int) -> Tuple[Rect, ...]:
    validated = []
    for index, rect in enumerate(rectangles):
        if not isinstance(rect, (tuple, list)) or len(rect) != 4 or any(type(value) is not int for value in rect):
            raise ComparisonError(
                "invalid_arguments",
                f"ignore rectangle {index} must contain four integer values",
                {"index": index, "rectangle": rect},
            )
        x, y, rect_width, rect_height = rect
        if x < 0 or y < 0 or rect_width <= 0 or rect_height <= 0:
            raise ComparisonError(
                "invalid_arguments",
                f"ignore rectangle {index} needs nonnegative coordinates and positive width and height",
                {"index": index, "rectangle": list(rect)},
            )
        if x + rect_width > width or y + rect_height > height:
            raise ComparisonError(
                "invalid_arguments",
                f"ignore rectangle {index} is outside the baseline image bounds ({width}x{height})",
                {
                    "index": index,
                    "rectangle": list(rect),
                    "baseline_size": {"width": width, "height": height},
                },
            )
        validated.append((x, y, rect_width, rect_height))
    return tuple(validated)


def _validate_threshold(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ComparisonError(
            "invalid_arguments", "max_diff_percent must be a finite number between 0 and 100"
        ) from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= 100:
        raise ComparisonError(
            "invalid_arguments", "max_diff_percent must be a finite number between 0 and 100"
        )
    return parsed


def compare_images(
    baseline: ImageSource,
    current: ImageSource,
    *,
    ignore_rects: Sequence[Rect] = (),
    resize: bool = False,
    max_diff_percent: Optional[float] = None,
    diff_out: Optional[Union[str, os.PathLike]] = None,
) -> Dict[str, Any]:
    """Compare two images and return machine-readable pixel and scale evidence.

    Ignore rectangles use baseline pixel coordinates. The image-size grid is
    always the baseline grid; when ``resize`` is true, current is resized to it.
    ``baseline`` and ``current`` may be PIL images or paths.
    """
    threshold = _validate_threshold(max_diff_percent)
    baseline_image = _coerce_image(baseline, "baseline")
    current_image = _coerce_image(current, "current")
    baseline_size = _size_evidence(baseline_image)
    current_size = _size_evidence(current_image)
    baseline_dpi = _dpi_evidence(baseline_image)
    current_dpi = _dpi_evidence(current_image)
    png_dpi_known = (
        str(getattr(baseline_image, "format", "")).upper() == "PNG"
        and str(getattr(current_image, "format", "")).upper() == "PNG"
        and baseline_dpi is not None
        and current_dpi is not None
    )
    dpi_mismatch = png_dpi_known and not _same_dpi(baseline_dpi, current_dpi)
    size_mismatch = baseline_image.size != current_image.size

    mismatch_details = {
        "baseline_size": baseline_size,
        "current_size": current_size,
        "baseline_dpi": baseline_dpi,
        "current_dpi": current_dpi,
        "scale_evidence": {
            "size_match": not size_mismatch,
            "png_dpi_comparable": bool(png_dpi_known),
            "dpi_mismatch": bool(dpi_mismatch),
            "coordinate_grid": "baseline",
        },
    }
    if size_mismatch and not resize:
        raise ComparisonError(
            "image_size_mismatch",
            "image sizes differ. Re-run with --resize to match the baseline pixel grid.",
            mismatch_details,
        )
    if dpi_mismatch and not resize:
        raise ComparisonError(
            "image_scale_mismatch",
            "PNG images have different known DPI scales. Re-run with --resize to normalize the pixel grid.",
            mismatch_details,
        )

    normalized = baseline_image.size != current_image.size or bool(dpi_mismatch)
    resized = baseline_image.size != current_image.size
    if resized:
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        current_image = current_image.resize(baseline_image.size, resampling)

    rectangles = _validate_rectangles(ignore_rects, baseline_image.width, baseline_image.height)
    include_mask = Image.new("L", baseline_image.size, 255)
    draw = ImageDraw.Draw(include_mask)
    for x, y, rect_width, rect_height in rectangles:
        draw.rectangle((x, y, x + rect_width - 1, y + rect_height - 1), fill=0)

    pixels_image_total = baseline_image.width * baseline_image.height
    included_histogram = include_mask.histogram()
    pixels_total = pixels_image_total - included_histogram[0]
    pixels_excluded = pixels_image_total - pixels_total
    if pixels_total == 0:
        raise ComparisonError(
            "invalid_arguments",
            "ignore rectangles exclude every pixel in the baseline image",
            {
                "baseline_size": baseline_size,
                "ignore_rects": [list(rect) for rect in rectangles],
                "pixels_image_total": pixels_image_total,
            },
        )

    difference = ImageChops.difference(baseline_image, current_image)
    channels = difference.split()
    magnitude = channels[0]
    for channel in channels[1:]:
        magnitude = ImageChops.lighter(magnitude, channel)
    histogram = magnitude.histogram(mask=include_mask)
    changed = pixels_total - histogram[0]
    avg_diff_percent = (
        sum(value * count for value, count in enumerate(histogram))
        / (255 * pixels_total)
        * 100
    )
    raw_percent_changed = changed / pixels_total * 100
    percent_changed = round(raw_percent_changed, 3)

    diff_path = None
    if diff_out is not None:
        diff_path = os.path.abspath(os.fspath(diff_out))
        try:
            os.makedirs(os.path.dirname(diff_path), exist_ok=True)
            overlay = Image.new("RGBA", current_image.size, (255, 0, 0, 0))
            overlay.putalpha(magnitude)
            diff_visualization = Image.alpha_composite(current_image, overlay).convert("RGBA")
            # Transparent ignored areas make it clear that they did not enter the diff.
            diff_visualization.putalpha(include_mask)
            diff_visualization.save(diff_path)
        except (OSError, ValueError) as exc:
            raise ComparisonError(
                "filesystem_error",
                f"could not write diff image: {exc}",
                {"diff_out": diff_path},
            ) from exc

    scale_evidence = {
        "size_match": not size_mismatch,
        "png_dpi_comparable": bool(png_dpi_known),
        "dpi_mismatch": bool(dpi_mismatch),
        "coordinate_grid": "baseline",
        "resize_requested": bool(resize),
        "normalization_applied": bool(resize and normalized),
    }
    result: Dict[str, Any] = {
        "diff_image": diff_path,
        "pixels_changed": changed,
        "pixels_total": pixels_total,
        "pixels_excluded": pixels_excluded,
        "pixels_image_total": pixels_image_total,
        "percent_changed": percent_changed,
        "avg_diff_percent": round(avg_diff_percent, 3),
        "size": {"width": baseline_image.width, "height": baseline_image.height},
        "baseline_size": baseline_size,
        "current_size": current_size,
        "baseline_dpi": baseline_dpi,
        "current_dpi": current_dpi,
        "scale_evidence": scale_evidence,
        "resized": resized,
        "options": {
            "resize": bool(resize),
            "ignore_rects": [list(rect) for rect in rectangles],
            "max_diff_percent": threshold,
        },
    }
    if threshold is not None:
        result.update(
            {
                "schema_version": 1,
                "operation": "verify",
                "status": "pass" if raw_percent_changed <= threshold else "fail",
                "thresholds": {"max_diff_percent": threshold},
            }
        )
    return result


def _write_json(path: Optional[str], payload: Dict[str, Any]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as report:
        json.dump(payload, report, indent=2)


def _emit_error(error: ComparisonError, json_out: Optional[str]) -> int:
    payload = {
        "schema_version": 1,
        "operation": "verify",
        "status": "error",
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
    }
    print(f"error: {error.message}", file=sys.stderr)
    try:
        _write_json(json_out, payload)
    except OSError as exc:
        print(f"error: could not write JSON report: {exc}", file=sys.stderr)
    print(json.dumps(payload))
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two images and output diff metrics.")
    parser.add_argument("baseline", help="Path to baseline image")
    parser.add_argument("current", help="Path to current image")
    parser.add_argument("--diff-out", help="Path to write diff image (PNG)")
    parser.add_argument("--json-out", help="Path to write JSON report")
    parser.add_argument("--resize", action="store_true", help="Resize current to baseline size")
    parser.add_argument(
        "--ignore-rect",
        action="append",
        nargs="?",
        const="",
        default=[],
        metavar="X,Y,WIDTH,HEIGHT",
        help="Exclude a baseline-pixel rectangle (may be repeated)",
    )
    parser.add_argument(
        "--max-diff-percent",
        type=percentage,
        help="Maximum changed pixels allowed, 0-100",
    )
    args = parser.parse_args()

    try:
        rectangles = []
        for index, value in enumerate(args.ignore_rect):
            try:
                rectangles.append(parse_rect(value))
            except ComparisonError as exc:
                raise ComparisonError(
                    "invalid_arguments",
                    f"invalid --ignore-rect value {value!r}: {exc.message}",
                    {"index": index, **exc.details},
                ) from exc

        result = compare_images(
            args.baseline,
            args.current,
            ignore_rects=rectangles,
            resize=args.resize,
            max_diff_percent=args.max_diff_percent,
            diff_out=args.diff_out,
        )
        result.update(
            {
                "baseline": os.path.abspath(args.baseline),
                "current": os.path.abspath(args.current),
            }
        )
        _write_json(args.json_out, result)
        print(json.dumps(result))
        return 1 if result.get("status") == "fail" else 0
    except ComparisonError as error:
        return _emit_error(error, args.json_out)


if __name__ == "__main__":
    raise SystemExit(main())
