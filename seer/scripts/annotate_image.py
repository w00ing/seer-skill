#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional

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


def _draw_rect(draw: ImageDraw.ImageDraw, ann: dict):
    x = float(ann.get("x", 0))
    y = float(ann.get("y", 0))
    w = float(ann.get("w", 0))
    h = float(ann.get("h", 0))
    outline = _parse_color(ann.get("color", "#FF3B30"))
    fill = ann.get("fill")
    fill_color = _parse_color(fill) if fill else None
    width = int(ann.get("width", 3))
    draw.rectangle([x, y, x + w, y + h], outline=outline, width=width, fill=fill_color)


def _draw_arrow(draw: ImageDraw.ImageDraw, ann: dict):
    x1 = float(ann.get("x1", 0))
    y1 = float(ann.get("y1", 0))
    x2 = float(ann.get("x2", 0))
    y2 = float(ann.get("y2", 0))
    color = _parse_color(ann.get("color", "#0A84FF"))
    width = int(ann.get("width", 3))
    head_len = float(ann.get("head_len", 12))
    head_width = float(ann.get("head_width", 8))

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


def _draw_text(draw: ImageDraw.ImageDraw, ann: dict):
    x = float(ann.get("x", 0))
    y = float(ann.get("y", 0))
    text = ann.get("text", "")
    if not text:
        return
    color = _parse_color(ann.get("color", "#FFFFFF"))
    size = int(ann.get("size", 14))
    font_name = ann.get("font")
    font = _load_font(font_name, size)
    padding = int(ann.get("padding", 4))
    bg = ann.get("bg")
    bg_color = _parse_color(bg) if bg else None

    bbox = draw.textbbox((x, y), text, font=font)
    if bg_color:
        rect = [
            bbox[0] - padding,
            bbox[1] - padding,
            bbox[2] + padding,
            bbox[3] + padding,
        ]
        draw.rectangle(rect, fill=bg_color)
    draw.text((x, y), text, fill=color, font=font)


def _load_spec(path: str) -> List[Dict]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "annotations" in data:
        return data["annotations"]
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
        annotations = _load_spec(args.spec)
    except Exception as exc:
        print(f"error: invalid spec: {exc}", file=sys.stderr)
        return 1

    image = Image.open(args.input).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        ann_type = str(ann.get("type", "")).lower()
        try:
            if ann_type == "rect":
                _draw_rect(draw, ann)
            elif ann_type == "arrow":
                _draw_arrow(draw, ann)
            elif ann_type == "text":
                _draw_text(draw, ann)
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
