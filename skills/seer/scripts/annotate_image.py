#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys
from typing import Dict, Optional

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    print("error: Pillow is required. Install with: python3 -m pip install pillow", file=sys.stderr)
    sys.exit(2)


def _parse_color(value: str):
    if not value:
        return None
    val = value.strip()
    if val.startswith("#"):
        hexval = val[1:]
        if len(hexval) == 6:
            r = int(hexval[0:2], 16)
            g = int(hexval[2:4], 16)
            b = int(hexval[4:6], 16)
            return (r, g, b, 255)
        if len(hexval) == 8:
            r = int(hexval[0:2], 16)
            g = int(hexval[2:4], 16)
            b = int(hexval[4:6], 16)
            a = int(hexval[6:8], 16)
            return (r, g, b, a)
        raise ValueError(f"unsupported hex color: {value}")
    if val.lower().startswith("rgba(") and val.endswith(")"):
        parts = [p.strip() for p in val[5:-1].split(",")]
        if len(parts) != 4:
            raise ValueError(f"invalid rgba color: {value}")
        r, g, b = [int(float(p)) for p in parts[:3]]
        a = float(parts[3])
        if a <= 1:
            a = int(round(a * 255))
        else:
            a = int(round(a))
        return (r, g, b, a)
    raise ValueError(f"unsupported color format: {value}")


def _load_font(font_name: Optional[str], size: int):
    if font_name:
        try:
            return ImageFont.truetype(font_name, size=size)
        except Exception:
            pass
    for candidate in ("Helvetica", "Arial"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _color_luma(color) -> float:
    if not color:
        return 0.0
    r, g, b, _ = color
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _auto_outline(color):
    if not color:
        return (0, 0, 0, 220)
    return (0, 0, 0, 220) if _color_luma(color) > 0.6 else (255, 255, 255, 220)


def _scale_default(value: float, scale: float, minimum: int = 1) -> int:
    return max(minimum, int(round(value * scale)))


def _resolve_scale(defaults: Optional[dict], image_size) -> float:
    defaults = defaults or {}
    if "scale" in defaults:
        try:
            return float(defaults["scale"])
        except Exception:
            pass
    auto_scale = defaults.get("auto_scale", True)
    if isinstance(auto_scale, str):
        auto_scale = auto_scale.strip().lower() not in ("0", "false", "no")
    if not auto_scale:
        return 1.0
    max_dim = max(image_size)
    return min(2.0, max(1.0, max_dim / 1200.0))


def _merge_defaults(defaults: Optional[dict], ann: dict) -> dict:
    if not defaults:
        return ann
    merged = dict(defaults)
    merged.update(ann)
    return merged


def _clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def _normalize_fit(fit):
    if fit is None:
        return None
    if isinstance(fit, bool):
        return {} if fit else None
    if isinstance(fit, str):
        return {"mode": fit}
    if isinstance(fit, dict):
        return dict(fit)
    return None


def _parse_region(region, ann: dict, image_size):
    width, height = image_size
    x = y = w = h = None
    if region:
        if isinstance(region, dict):
            x = region.get("x")
            y = region.get("y")
            w = region.get("w")
            h = region.get("h")
        elif isinstance(region, (list, tuple)) and len(region) >= 4:
            x, y, w, h = region[:4]
    if x is None or y is None or w is None or h is None:
        x = ann.get("x")
        y = ann.get("y")
        w = ann.get("w")
        h = ann.get("h")
    if x is None or y is None or w is None or h is None:
        return (0, 0, width, height)
    try:
        x = float(x)
        y = float(y)
        w = float(w)
        h = float(h)
    except Exception:
        return (0, 0, width, height)
    x0 = _clamp(int(round(x)), 0, width)
    y0 = _clamp(int(round(y)), 0, height)
    x1 = _clamp(int(round(x + w)), 0, width)
    y1 = _clamp(int(round(y + h)), 0, height)
    if x1 <= x0 or y1 <= y0:
        return (0, 0, width, height)
    return (x0, y0, x1, y1)


def _fit_bbox_luma(image_rgb: Image.Image, region, threshold: float, target: str, min_pixels: int):
    pixels = image_rgb.load()
    x0, y0, x1, y1 = region
    minx = miny = 10**9
    maxx = maxy = -1
    count = 0
    dark = target != "light"
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = pixels[x, y]
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if (luma <= threshold) if dark else (luma >= threshold):
                count += 1
                if x < minx:
                    minx = x
                if y < miny:
                    miny = y
                if x > maxx:
                    maxx = x
                if y > maxy:
                    maxy = y
    if count < max(1, min_pixels) or maxx < 0:
        return None
    return (minx, miny, maxx, maxy)


def _fit_bbox_color(image_rgb: Image.Image, region, color, tolerance: float, min_pixels: int):
    if not color:
        return None
    pixels = image_rgb.load()
    x0, y0, x1, y1 = region
    minx = miny = 10**9
    maxx = maxy = -1
    count = 0
    r0, g0, b0, _ = color
    tol = max(0.0, float(tolerance))
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = pixels[x, y]
            if max(abs(r - r0), abs(g - g0), abs(b - b0)) <= tol:
                count += 1
                if x < minx:
                    minx = x
                if y < miny:
                    miny = y
                if x > maxx:
                    maxx = x
                if y > maxy:
                    maxy = y
    if count < max(1, min_pixels) or maxx < 0:
        return None
    return (minx, miny, maxx, maxy)


def _expand_bbox(bbox, pad: float, image_size):
    if not bbox:
        return None
    width, height = image_size
    x0, y0, x1, y1 = bbox
    pad = float(pad or 0)
    x0 = _clamp(int(round(x0 - pad)), 0, width)
    y0 = _clamp(int(round(y0 - pad)), 0, height)
    x1 = _clamp(int(round(x1 + pad)), 0, width)
    y1 = _clamp(int(round(y1 + pad)), 0, height)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _apply_fit(ann: dict, image_rgb: Image.Image) -> dict:
    fit = _normalize_fit(ann.get("fit"))
    if not fit:
        return ann
    mode = str(fit.get("mode", "luma")).lower()
    region = _parse_region(fit.get("region"), ann, image_rgb.size)
    min_pixels = int(fit.get("min_pixels", 30))
    bbox = None
    if mode == "luma":
        threshold = float(fit.get("threshold", 160))
        target = str(fit.get("target", "dark")).lower()
        bbox = _fit_bbox_luma(image_rgb, region, threshold, target, min_pixels)
    elif mode == "color":
        color_value = fit.get("color") or fit.get("target_color")
        color = _parse_color(color_value) if color_value else None
        tolerance = float(fit.get("tolerance", 18))
        bbox = _fit_bbox_color(image_rgb, region, color, tolerance, min_pixels)
    else:
        return ann
    pad = float(fit.get("pad", 0))
    bbox = _expand_bbox(bbox, pad, image_rgb.size)
    if not bbox:
        print(f"warn: fit({mode}) did not find pixels; using original bounds", file=sys.stderr)
        return ann
    x0, y0, x1, y1 = bbox
    updated = dict(ann)
    updated["x"] = x0
    updated["y"] = y0
    updated["w"] = x1 - x0
    updated["h"] = y1 - y0
    return updated


def _apply_opacity(color, opacity):
    if not color or opacity is None:
        return color
    try:
        alpha = float(opacity)
    except Exception:
        return color
    if alpha <= 1:
        alpha = int(round(alpha * 255))
    else:
        alpha = int(round(alpha))
    r, g, b, _ = color
    return (r, g, b, max(0, min(255, alpha)))


def _resolve_dim_color(ann: dict, defaults: Optional[dict]):
    defaults = defaults or {}
    value = ann.get("color") or ann.get("dim_color") or defaults.get("dim_color") or "rgba(0,0,0,0.45)"
    color = _parse_color(value)
    opacity = ann.get("opacity", defaults.get("dim_opacity"))
    return _apply_opacity(color, opacity)


def _draw_spotlight(overlay: Image.Image, ann: dict, scale: float, defaults: Optional[dict]):
    color = _resolve_dim_color(ann, defaults)
    if not color:
        return overlay
    layer = Image.new("RGBA", overlay.size, color)
    draw = ImageDraw.Draw(layer)
    padding = float(ann.get("padding", defaults.get("dim_padding", 0) if defaults else 0))
    padding = padding * scale if padding else 0.0
    radius = float(ann.get("radius", defaults.get("dim_radius", 0) if defaults else 0))
    radius = radius * scale if radius else 0.0
    x = float(ann.get("x", 0)) - padding
    y = float(ann.get("y", 0)) - padding
    w = float(ann.get("w", 0)) + padding * 2
    h = float(ann.get("h", 0)) + padding * 2
    rect = [x, y, x + w, y + h]
    if radius > 0:
        draw.rounded_rectangle(rect, radius=radius, fill=(0, 0, 0, 0))
    else:
        draw.rectangle(rect, fill=(0, 0, 0, 0))
    return Image.alpha_composite(overlay, layer)


def _draw_rect(draw: ImageDraw.ImageDraw, ann: dict, scale: float):
    x = float(ann.get("x", 0))
    y = float(ann.get("y", 0))
    w = float(ann.get("w", 0))
    h = float(ann.get("h", 0))
    outline = _parse_color(ann.get("color", "#FF3B30"))
    fill = ann.get("fill")
    fill_color = _parse_color(fill) if fill else None
    width = int(ann.get("width", _scale_default(3, scale, minimum=2)))

    outline_enabled = ann.get("outline", True)
    outline_width = int(ann.get("outline_width", max(2, round(width * 0.6))))
    outline_color = _parse_color(ann.get("outline_color")) if ann.get("outline_color") else _auto_outline(outline)
    if outline_enabled and outline_color:
        draw.rectangle(
            [x, y, x + w, y + h],
            outline=outline_color,
            width=width + outline_width * 2,
        )

    draw.rectangle([x, y, x + w, y + h], outline=outline, width=width, fill=fill_color)


def _draw_arrow_primitive(
    draw: ImageDraw.ImageDraw,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color,
    width: int,
    head_len: float,
    head_width: float,
):
    angle = math.atan2(y2 - y1, x2 - x1)
    back_x = x2 - head_len * math.cos(angle)
    back_y = y2 - head_len * math.sin(angle)

    left_angle = angle + math.pi / 2
    right_angle = angle - math.pi / 2
    left_x = back_x + (head_width / 2) * math.cos(left_angle)
    left_y = back_y + (head_width / 2) * math.sin(left_angle)
    right_x = back_x + (head_width / 2) * math.cos(right_angle)
    right_y = back_y + (head_width / 2) * math.sin(right_angle)

    draw.line([x1, y1, back_x, back_y], fill=color, width=width)
    draw.polygon([(x2, y2), (left_x, left_y), (right_x, right_y)], fill=color)


def _draw_arrow(draw: ImageDraw.ImageDraw, ann: dict, scale: float):
    x1 = float(ann.get("x1", 0))
    y1 = float(ann.get("y1", 0))
    x2 = float(ann.get("x2", 0))
    y2 = float(ann.get("y2", 0))
    color = _parse_color(ann.get("color", "#0A84FF"))
    width = int(ann.get("width", _scale_default(3, scale, minimum=2)))
    head_len = float(ann.get("head_len", _scale_default(12, scale, minimum=6)))
    head_width = float(ann.get("head_width", _scale_default(8, scale, minimum=5)))

    outline_enabled = ann.get("outline", True)
    outline_width = int(ann.get("outline_width", max(2, round(width * 0.6))))
    outline_color = _parse_color(ann.get("outline_color")) if ann.get("outline_color") else _auto_outline(color)
    if outline_enabled and outline_color:
        _draw_arrow_primitive(
            draw,
            x1,
            y1,
            x2,
            y2,
            outline_color,
            width + outline_width * 2,
            head_len + outline_width * 2,
            head_width + outline_width * 2,
        )

    _draw_arrow_primitive(draw, x1, y1, x2, y2, color, width, head_len, head_width)


def _draw_text_outline(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: ImageFont.ImageFont,
    stroke_width: int,
    stroke_fill,
):
    if stroke_width <= 0:
        return
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx == 0 and dy == 0:
                continue
            if dx * dx + dy * dy > stroke_width * stroke_width:
                continue
            draw.text((x + dx, y + dy), text, fill=stroke_fill, font=font)


def _draw_text(draw: ImageDraw.ImageDraw, ann: dict, scale: float):
    x = float(ann.get("x", 0))
    y = float(ann.get("y", 0))
    text = ann.get("text", "")
    if not text:
        return
    color = _parse_color(ann.get("color", "#FFFFFF"))
    size = int(ann.get("size", _scale_default(14, scale, minimum=10)))
    font_name = ann.get("font")
    font = _load_font(font_name, size)
    padding = int(ann.get("padding", _scale_default(4, scale, minimum=2)))
    bg = ann.get("bg")
    if not bg:
        bg = ann.get("text_bg")
    bg_color = _parse_color(bg) if bg else None
    outline_enabled = ann.get("outline", True)
    outline_width = int(ann.get("outline_width", max(1, round(size * 0.12))))
    outline_color = _parse_color(ann.get("outline_color")) if ann.get("outline_color") else _auto_outline(color)

    bbox = draw.textbbox((x, y), text, font=font)
    if bg_color:
        rect = [
            bbox[0] - padding,
            bbox[1] - padding,
            bbox[2] + padding,
            bbox[3] + padding,
        ]
        draw.rectangle(rect, fill=bg_color)
    if outline_enabled and outline_color:
        _draw_text_outline(draw, x, y, text, font, outline_width, outline_color)
    draw.text((x, y), text, fill=color, font=font)


def _load_spec(path: str) -> Dict:
    if path == "-":
        raw = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    data = json.loads(raw)
    if isinstance(data, list):
        return {"annotations": data, "defaults": {}}
    if isinstance(data, dict) and "annotations" in data:
        return {"annotations": data["annotations"], "defaults": data.get("defaults") or {}}
    raise ValueError("spec must be a list or an object with 'annotations'")


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate an image with arrows, rectangles, and text.")
    parser.add_argument("input", help="Input PNG path")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--spec", required=True, help="JSON file path (or - for stdin)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 1

    try:
        spec = _load_spec(args.spec)
    except Exception as exc:
        print(f"error: invalid spec: {exc}", file=sys.stderr)
        return 1

    image = Image.open(args.input).convert("RGBA")
    image_rgb = image.convert("RGB")
    defaults = spec.get("defaults") or {}
    base_scale = _resolve_scale(defaults, image.size)

    annotations = spec.get("annotations", [])
    spotlights = []
    others = []
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        ann_type = str(ann.get("type", "")).lower()
        if ann_type in ("spotlight", "focus", "dim"):
            spotlights.append(ann)
        else:
            others.append(ann)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for ann in spotlights:
        ann = _merge_defaults(defaults, ann)
        ann = _apply_fit(ann, image_rgb)
        ann_scale = float(ann.get("scale", base_scale))
        try:
            overlay = _draw_spotlight(overlay, ann, ann_scale, defaults)
        except Exception as exc:
            print(f"warn: failed annotation spotlight: {exc}", file=sys.stderr)

    draw = ImageDraw.Draw(overlay)
    for ann in others:
        ann = _merge_defaults(defaults, ann)
        ann_scale = float(ann.get("scale", base_scale))
        ann_type = str(ann.get("type", "")).lower()
        try:
            if ann_type == "rect":
                ann = _apply_fit(ann, image_rgb)
                _draw_rect(draw, ann, ann_scale)
            elif ann_type == "arrow":
                _draw_arrow(draw, ann, ann_scale)
            elif ann_type == "text":
                _draw_text(draw, ann, ann_scale)
        except Exception as exc:
            print(f"warn: failed annotation {ann_type}: {exc}", file=sys.stderr)

    combined = Image.alpha_composite(image, overlay)
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    combined.convert("RGB").save(args.output)
    print(os.path.abspath(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
