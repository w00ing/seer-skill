#!/usr/bin/env python3
import argparse
import json
import os
import sys

try:
    from PIL import Image, ImageChops
except Exception:
    print("error: Pillow is required. Install with: python3 -m pip install pillow", file=sys.stderr)
    sys.exit(2)


def load_image(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two images and output diff metrics.")
    parser.add_argument("baseline", help="Path to baseline image")
    parser.add_argument("current", help="Path to current image")
    parser.add_argument("--diff-out", help="Path to write diff image (PNG)")
    parser.add_argument("--json-out", help="Path to write JSON report")
    parser.add_argument("--resize", action="store_true", help="Resize current to baseline size")
    args = parser.parse_args()

    if not os.path.exists(args.baseline):
        print(f"error: baseline not found: {args.baseline}", file=sys.stderr)
        return 1
    if not os.path.exists(args.current):
        print(f"error: current not found: {args.current}", file=sys.stderr)
        return 1

    baseline = load_image(args.baseline)
    current = load_image(args.current)
    resized = False

    if baseline.size != current.size:
        if args.resize:
            current = current.resize(baseline.size, Image.LANCZOS)
            resized = True
        else:
            print(
                "error: image sizes differ. Re-run with --resize to match baseline size.",
                file=sys.stderr,
            )
            return 1

    diff = ImageChops.difference(baseline, current)
    gray = diff.convert("L")
    hist = gray.histogram()
    total = sum(hist)
    changed = total - hist[0] if total else 0
    avg = sum(i * c for i, c in enumerate(hist)) / (255 * total) if total else 0.0

    percent_changed = (changed / total * 100) if total else 0.0
    avg_diff_percent = avg * 100

    diff_path = None
    if args.diff_out:
        diff_path = args.diff_out
        os.makedirs(os.path.dirname(diff_path), exist_ok=True)
        overlay = Image.new("RGBA", current.size, (255, 0, 0, 0))
        overlay.putalpha(gray)
        diff_vis = Image.alpha_composite(current, overlay).convert("RGB")
        diff_vis.save(diff_path)

    result = {
        "baseline": os.path.abspath(args.baseline),
        "current": os.path.abspath(args.current),
        "diff_image": os.path.abspath(diff_path) if diff_path else None,
        "percent_changed": round(percent_changed, 3),
        "avg_diff_percent": round(avg_diff_percent, 3),
        "size": {"width": baseline.size[0], "height": baseline.size[1]},
        "resized": resized,
    }

    if args.json_out:
        os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
