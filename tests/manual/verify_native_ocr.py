#!/usr/bin/env python3
"""Opt-in Vision test on synthetic pixels; does not validate window capture."""
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/seer/scripts"))
from ui_inspect import _get_native_binary, _run_native


def main():
    qa = ROOT / ".seer/qa"
    qa.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="v07-synthetic-ocr-", dir=qa))
    deadline = time.monotonic() + 30
    binary = _get_native_binary(deadline, ROOT / ".seer")
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 42)
    image = Image.new("RGB", (800, 240), "white")
    ImageDraw.Draw(image).text((40, 70), "Canvas Evidence", font=font, fill="black")
    image.save(output / "text.png")
    Image.new("RGB", (800, 240), "white").save(output / "blank.png")
    text = _run_native(binary, ["ocr", "--image", str(output / "text.png")], deadline)
    blank = _run_native(binary, ["ocr", "--image", str(output / "blank.png")], deadline)
    found = [item for item in text["elements"] if item["value"] == "Canvas Evidence"]
    assert text["complete"] and found and found[0]["confidence"] >= 0.8, text
    bounds = found[0]["bounds"]
    assert 30 <= bounds["x"] <= 60 and 60 <= bounds["y"] <= 130, bounds
    assert bounds["width"] > 100 and bounds["height"] > 10, bounds
    assert blank["complete"] and not blank["elements"], blank
    report = {"status": "pass", "platform": platform.platform(),
              "scope": "Vision recognition and top-left pixel bounds on synthetic images only",
              "native_binary": str(binary), "text": text, "blank": blank}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(output / "report.json")


if __name__ == "__main__":
    main()
