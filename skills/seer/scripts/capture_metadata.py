"""Hash-bound local provenance for captures; no metadata is inferred for imports."""
import hashlib
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def describe_capture(path, *, window_id, process, started_at):
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        size = {"width": image.width, "height": image.height}
        dpi = image.info.get("dpi")
    if not (isinstance(dpi, (tuple, list)) and len(dpi) == 2
            and all(isinstance(n, (int, float)) and math.isfinite(n) and n > 0 for n in dpi)):
        dpi = None
    return {
        "schema_version": 1,
        "source": "seer.capture",
        "image_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "capture_started_at": started_at,
        "captured_at": utc_now(),
        "window_id": window_id,
        "process": process,
        "size": size,
        "dpi": list(dpi) if dpi else None,
    }


def publish_image(source, destination, metadata):
    """Stage both files before publishing; a checksum binds the sidecar to pixels."""
    destination = Path(destination).expanduser().resolve()
    sidecar = Path(str(destination) + ".seer.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_dir() or sidecar.is_dir():
        raise IsADirectoryError(f"capture destination is a directory: {destination}")
    with tempfile.TemporaryDirectory(prefix=".seer-publish-", dir=destination.parent) as temporary:
        image = Path(temporary) / "current.png"
        info = Path(temporary) / "metadata.json"
        shutil.copyfile(source, image)
        info.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        # Publish metadata first: interruption can only leave a detectable hash
        # mismatch, never provenance silently attributed to the wrong image.
        os.replace(info, sidecar)
        os.replace(image, destination)
    return {"current": str(destination), "metadata": str(sidecar)}
