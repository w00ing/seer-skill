#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal


@dataclass(frozen=True)
class CanvasPreset:
    name: str
    width: int
    height: int
    grid_size: int = 20


PRESETS: dict[str, CanvasPreset] = {
    "mobile": CanvasPreset("mobile", 390, 844, 20),
    "desktop": CanvasPreset("desktop", 1440, 900, 20),
    "tablet": CanvasPreset("tablet", 834, 1194, 20),
}

Fidelity = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    container: str
    border: str
    text: str
    muted_text: str


THEMES: dict[str, Theme] = {
    "classic": Theme(
        name="classic",
        background="#ffffff",
        container="#f5f5f5",
        border="#9e9e9e",
        text="#424242",
        muted_text="#666666",
    ),
    "high_contrast": Theme(
        name="high_contrast",
        background="#ffffff",
        container="#eeeeee",
        border="#212121",
        text="#000000",
        muted_text="#333333",
    ),
    "blueprint": Theme(
        name="blueprint",
        background="#1a237e",
        container="#3949ab",
        border="#7986cb",
        text="#ffffff",
        muted_text="#c5cae9",
    ),
}


COMPONENT_TYPES = (
    "screen",
    "header",
    "tabs",
    "section",
    "text",
    "input",
    "button",
    "dropdown",
    "textarea",
    "checkbox",
    "radio",
    "toggle",
    "chips",
    "card",
    "list",
    "image",
    "divider",
    "footer",
    "lib",
    "library",
)


def _slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-z0-9._-]+", "", slug)
    return slug or "wireframe"


def _now_ms() -> int:
    if _NOW_MS is not None:
        return _NOW_MS
    return int(time.time() * 1000)


_NOW_MS: int | None = None


def _stable_seed(*parts: str) -> int:
    h = hashlib.sha256()
    for part in parts:
        if part is None:
            continue
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    # Keep within signed 32-bit range for consistent downstream usage.
    return int.from_bytes(h.digest()[:4], "big") & 0x7FFFFFFF


def _new_id() -> str:
    # Excalidraw IDs are opaque strings; any unique-ish string works.
    if _ID_RNG is None:
        return uuid.uuid4().hex[:20]
    return f"{_ID_RNG.getrandbits(80):020x}"


_ID_RNG: random.Random | None = None


def _default_library_path() -> Path:
    # scripts/ -> seer/
    seer_root = Path(__file__).resolve().parent.parent
    # Prefer a richer UI kit if present.
    preferred = seer_root / "assets" / "excalidraw" / "wireframe-ui-kit.excalidrawlib"
    if preferred.exists():
        return preferred
    return seer_root / "assets" / "excalidraw" / "basic-ux-wireframing-elements.excalidrawlib"


def _default_excalidraw_output_dir(out_root: str) -> str:
    return os.path.join(out_root, "excalidraw")


def _round_to(value: float, step: int) -> int:
    if step <= 0:
        return int(round(value))
    return int(round(value / step) * step)


def _text_size(text: str, font_size: int) -> tuple[int, int]:
    # Excalidraw will usually recalc text metrics; keep this conservative.
    lines = text.splitlines() or [""]
    max_len = max((len(line) for line in lines), default=0)
    width = int(max(24, min(2400, max_len * font_size * 0.62)))
    height = int(max(font_size + 8, len(lines) * font_size * 1.35))
    return width, height


def _calc_label_width(text: str, font_size: int) -> int:
    # BMAD heuristic: (len × fontSize × 0.6) + 20, rounded to 10px.
    return _round_to(len(text) * font_size * 0.6 + 20, 10)


def _bbox_for_element(el: dict[str, Any]) -> tuple[float, float, float, float]:
    x = float(el.get("x", 0.0))
    y = float(el.get("y", 0.0))
    w = float(el.get("width", 0.0))
    h = float(el.get("height", 0.0))
    x0 = min(x, x + w)
    x1 = max(x, x + w)
    y0 = min(y, y + h)
    y1 = max(y, y + h)
    return x0, y0, x1, y1


def _bbox_for_elements(elements: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    if not elements:
        return 0.0, 0.0, 0.0, 0.0
    x0, y0, x1, y1 = _bbox_for_element(elements[0])
    for el in elements[1:]:
        ex0, ey0, ex1, ey1 = _bbox_for_element(el)
        x0 = min(x0, ex0)
        y0 = min(y0, ey0)
        x1 = max(x1, ex1)
        y1 = max(y1, ey1)
    return x0, y0, x1, y1


@dataclass(frozen=True)
class LibraryItem:
    name: str
    id: str
    elements: list[dict[str, Any]]


class ExcalidrawLibrary:
    def __init__(self, items: list[LibraryItem]):
        self._items = items
        self._by_name: dict[str, LibraryItem] = {it.name.strip().lower(): it for it in items if it.name}

    @property
    def items(self) -> list[LibraryItem]:
        return list(self._items)

    def find(self, query: str) -> LibraryItem | None:
        q = query.strip().lower()
        if not q:
            return None
        if q in self._by_name:
            return self._by_name[q]
        for it in self._items:
            if q in (it.name or "").lower():
                return it
        return None


def load_excalidraw_library(path: Path) -> ExcalidrawLibrary:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_items = data.get("libraryItems") or data.get("library") or []
    items: list[LibraryItem] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or it.get("id") or "").strip()
        item_id = (it.get("id") or "").strip() or _new_id()
        elements = it.get("elements") or []
        if not isinstance(elements, list) or not elements:
            continue
        els: list[dict[str, Any]] = [e for e in elements if isinstance(e, dict)]
        if not els:
            continue
        items.append(LibraryItem(name=name or item_id, id=item_id, elements=els))
    return ExcalidrawLibrary(items)


DEFAULT_LIBRARY_COMPONENT_QUERIES: dict[str, list[str]] = {
    # Keep this conservative: only use library items that are easy to label reliably.
    "header": ["navigation bar"],
    "button": ["button", "Filled button (text only)", "Outlined button (text only)"],
    "input": ["textfield", "Text field with placeholder", "Text field with text", "search", "Search field"],
    "tabs": ["tabs"],
    "dropdown": ["dropdown", "select"],
    "textarea": ["textarea"],
    "checkbox": ["checkbox-off", "checkbox-on", "Checkbox Unchecked", "Checkbox Checked"],
    "radio": ["radiobutton-off"],
    "toggle": ["toggle-off"],
    "chips": ["chips"],
    "card": ["banner", "carousel banner"],
    "image": ["product image", "gallery-3-a", "gallery-3-b", "Image placeholder", "Image placeholder (simple)"],
    "footer": ["tab bar"],
}


def _pick_library_item_for_component(
    library: ExcalidrawLibrary, component_type: str, label: str | None = None
) -> LibraryItem | None:
    label = (label or "").strip().lower()

    def _looks_truthy(s: str) -> bool:
        return bool(re.search(r"\b(on|yes|true|enabled|checked|selected)\b", s))

    def _looks_falsy(s: str) -> bool:
        return bool(re.search(r"\b(off|no|false|disabled|unchecked|unselected)\b", s))

    # Small heuristics to pick better primitives.
    if component_type == "input" and label:
        # Prefer a plain text field for non-search inputs.
        it = library.find("textfield") or library.find("Text field with placeholder") or library.find("Text field with text")
        if it:
            return it
        if "search" in label:
            it = library.find("search") or library.find("Search field") or library.find("Search Input")
            if it:
                return it

    if component_type == "header":
        it = library.find("navigation bar")
        if it:
            return it

    if component_type == "button":
        it = library.find("button") or library.find("Filled button (text only)") or library.find("Outlined button (text only)")
        if it:
            return it

    if component_type == "dropdown":
        it = library.find("dropdown") or library.find("select")
        if it:
            return it

    if component_type == "textarea":
        it = library.find("textarea")
        if it:
            return it

    if component_type == "checkbox":
        if label and _looks_truthy(label) and not _looks_falsy(label):
            it = library.find("checkbox-on") or library.find("Checkbox Checked")
            if it:
                return it
        it = library.find("checkbox-off") or library.find("Checkbox Unchecked")
        if it:
            return it

    if component_type == "radio":
        if label and _looks_truthy(label) and not _looks_falsy(label):
            it = library.find("radiobutton-on")
            if it:
                return it
        it = library.find("radiobutton-off")
        if it:
            return it

    if component_type == "toggle":
        if label and _looks_truthy(label) and not _looks_falsy(label):
            it = library.find("toggle-on")
            if it:
                return it
        it = library.find("toggle-off")
        if it:
            return it

    if component_type == "chips":
        it = library.find("chips")
        if it:
            return it

    if component_type == "card":
        it = library.find("banner") or library.find("carousel banner")
        if it:
            return it

    if component_type == "tabs":
        it = library.find("tabs") or library.find("tab bar")
        if it:
            return it

    if component_type == "image":
        it = library.find("product image") or library.find("gallery-3-a") or library.find("gallery-3-b") or library.find("Image placeholder")
        if it:
            return it

    if component_type == "footer":
        it = library.find("tab bar")
        if it:
            return it

    for query in DEFAULT_LIBRARY_COMPONENT_QUERIES.get(component_type, []):
        found = library.find(query)
        if found:
            return found
    return None


def _rewrite_library_label(builder: "ExcalidrawBuilder", group_elements: list[dict[str, Any]], new_text: str) -> None:
    new_text = new_text.strip()
    if not new_text:
        return
    texts = [el for el in group_elements if el.get("type") == "text" and isinstance(el.get("text"), str)]
    if not texts:
        return

    preferred = {"button", "search", "placeholder", "text", "title", "label", "name"}

    def score(el: dict[str, Any]) -> tuple[int, float]:
        t = (el.get("text") or "").strip().lower()
        s0 = 2 if t in preferred else 0
        if el.get("containerId"):
            s0 += 1
        return (s0, float(el.get("width") or 0))

    target = sorted(texts, key=score, reverse=True)[0]
    _set_library_text(builder, group_elements, target, new_text)

def _set_library_text(builder: "ExcalidrawBuilder", group_elements: list[dict[str, Any]], target: dict[str, Any], new_text: str) -> None:
    new_text = (new_text or "").strip()
    if not new_text:
        return

    target["text"] = new_text
    target["originalText"] = new_text

    container_id = target.get("containerId")
    if container_id:
        by_id = {el.get("id"): el for el in group_elements}
        container = by_id.get(container_id)
        if isinstance(container, dict):
            cw = float(container.get("width") or 0)
            cx = float(container.get("x") or 0)
            ch = float(container.get("height") or 0)
            cy = float(container.get("y") or 0)

            font_size = int(float(target.get("fontSize") or 16))
            new_w = min(int(cw - builder.grid * 2), _calc_label_width(new_text, font_size))
            new_w = max(24, new_w)
            target["width"] = float(_round_to(new_w, 10))

            if target.get("textAlign") == "center":
                # Important: do NOT grid-snap library internals; it distorts layout.
                target["x"] = float(_round_to(cx + (cw - float(target["width"])) / 2, 1))
                target["y"] = float(_round_to(cy + (ch - float(target.get("height") or 20)) / 2, 1))
            else:
                target["x"] = float(_round_to(cx + builder.grid, 1))
    else:
        # Standalone library text often has a fixed width; update it to avoid clipping.
        try:
            font_size = int(float(target.get("fontSize") or 16))
        except Exception:
            font_size = 16
        target["width"] = float(_round_to(_calc_label_width(new_text, font_size), 10))
        target["height"] = float(_round_to(max(float(target.get("height") or 0), font_size * 1.6), 10))


def _rewrite_library_tabs_labels(builder: "ExcalidrawBuilder", group_elements: list[dict[str, Any]], labels: list[str]) -> None:
    labels = [l.strip() for l in labels if l.strip()]
    if not labels:
        return
    texts = [el for el in group_elements if el.get("type") == "text" and isinstance(el.get("text"), str)]
    if not texts:
        return

    # Assign left-to-right.
    texts = sorted(texts, key=lambda el: float(el.get("x") or 0))
    labels = labels[: len(texts)]

    by_id = {el.get("id"): el for el in group_elements if isinstance(el.get("id"), str)}

    for el, label in zip(texts, labels, strict=False):
        _set_library_text(builder, group_elements, el, label)


def _rewrite_library_section_title(builder: "ExcalidrawBuilder", group_elements: list[dict[str, Any]], title: str) -> None:
    title = (title or "").strip()
    if not title:
        return
    texts = [el for el in group_elements if el.get("type") == "text" and isinstance(el.get("text"), str)]
    if not texts:
        return
    texts = sorted(texts, key=lambda el: (float(el.get("y") or 0), float(el.get("x") or 0)))
    # First line becomes the title; hide the optional "View All >" if present.
    _set_library_text(builder, group_elements, texts[0], title.upper())
    if len(texts) > 1:
        # Hide but keep for consistency.
        texts[1]["opacity"] = 0


def _rewrite_library_footer_labels(builder: "ExcalidrawBuilder", group_elements: list[dict[str, Any]], labels: list[str]) -> None:
    labels = [l.strip() for l in labels if l.strip()]
    if not labels:
        return
    texts = [el for el in group_elements if el.get("type") == "text" and isinstance(el.get("text"), str)]
    if not texts:
        return
    texts = sorted(texts, key=lambda el: float(el.get("x") or 0))
    for idx, el in enumerate(texts):
        if idx >= len(labels):
            el["opacity"] = 0
            continue
        new_text = labels[idx]
        cx = float(el.get("x") or 0) + float(el.get("width") or 0) / 2
        try:
            font_size = int(float(el.get("fontSize") or 12))
        except Exception:
            font_size = 12
        new_w = float(_round_to(_calc_label_width(new_text, font_size), 10))
        el["text"] = new_text
        el["originalText"] = new_text
        el["width"] = new_w
        el["height"] = float(_round_to(max(float(el.get("height") or 0), font_size * 1.6), 10))
        el["x"] = float(_round_to(cx - new_w / 2, 1))


def _rewrite_library_label_and_placeholder(
    builder: "ExcalidrawBuilder",
    group_elements: list[dict[str, Any]],
    *,
    label: str,
    placeholder: str,
) -> None:
    label = (label or "").strip()
    placeholder = (placeholder or "").strip()
    if not label and not placeholder:
        return

    texts = [el for el in group_elements if el.get("type") == "text" and isinstance(el.get("text"), str)]
    if not texts:
        return

    label_targets = [t for t in texts if not t.get("containerId")]
    placeholder_targets = [t for t in texts if t.get("containerId")]

    if label and label_targets:
        target = sorted(label_targets, key=lambda el: (float(el.get("y") or 0), -float(el.get("width") or 0)))[0]
        _set_library_text(builder, group_elements, target, label)
    elif label_targets:
        for el in label_targets:
            el["opacity"] = 0

    if placeholder and placeholder_targets:
        # Prefer the widest placeholder.
        target = sorted(placeholder_targets, key=lambda el: float(el.get("width") or 0), reverse=True)[0]
        _set_library_text(builder, group_elements, target, placeholder)


def instantiate_library_item(
    *,
    builder: "ExcalidrawBuilder",
    item: LibraryItem,
    x: float,
    y: float,
    label_override: str | None,
    seer_label: str | None,
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = json.loads(json.dumps(item.elements))

    id_map: dict[str, str] = {}
    group_map: dict[str, str] = {}
    for el in copied:
        old_id = str(el.get("id") or "")
        if old_id:
            id_map[old_id] = _new_id()
        for gid in el.get("groupIds") or []:
            if isinstance(gid, str) and gid and gid not in group_map:
                group_map[gid] = _new_id()

    min_x, min_y, _, _ = _bbox_for_elements(copied)
    # Align the *group* placement to the grid, but keep internal offsets intact.
    dx = builder.snap(x) - min_x
    dy = builder.snap(y) - min_y

    now = _now_ms()

    for el in copied:
        old_id = str(el.get("id") or "")
        if old_id in id_map:
            el["id"] = id_map[old_id]

        el["seed"] = builder._seed()
        el["versionNonce"] = builder._seed()
        el["updated"] = now
        el["isDeleted"] = False
        el["locked"] = False

        el.setdefault("customData", {})
        if isinstance(el["customData"], dict):
            el["customData"].setdefault("seerSource", "library")
            if seer_label:
                el["customData"].setdefault("seerLabel", seer_label)

        gids = []
        for gid in el.get("groupIds") or []:
            if isinstance(gid, str) and gid in group_map:
                gids.append(group_map[gid])
        el["groupIds"] = gids

        if "x" in el:
            el["x"] = float(_round_to(float(el["x"]) + dx, 1))
        if "y" in el:
            el["y"] = float(_round_to(float(el["y"]) + dy, 1))

        container_id = el.get("containerId")
        if isinstance(container_id, str) and container_id in id_map:
            el["containerId"] = id_map[container_id]

        if isinstance(el.get("boundElements"), list):
            new_bound = []
            for b in el["boundElements"]:
                if not isinstance(b, dict):
                    continue
                bid = b.get("id")
                if isinstance(bid, str) and bid in id_map:
                    b = dict(b)
                    b["id"] = id_map[bid]
                new_bound.append(b)
            el["boundElements"] = new_bound

        for key in ("startBinding", "endBinding"):
            b = el.get(key)
            if isinstance(b, dict):
                eid = b.get("elementId")
                if isinstance(eid, str) and eid in id_map:
                    b = dict(b)
                    b["elementId"] = id_map[eid]
                    el[key] = b

    # Normalize container/text invariants.
    by_id = {el.get("id"): el for el in copied if isinstance(el.get("id"), str)}
    for el in copied:
        if el.get("type") != "text":
            continue
        container_id = el.get("containerId")
        if not isinstance(container_id, str) or not container_id:
            continue
        container = by_id.get(container_id)
        if not isinstance(container, dict):
            continue
        el["groupIds"] = container.get("groupIds") or []
        bound = container.get("boundElements")
        if not isinstance(bound, list):
            bound = []
        if not any(isinstance(b, dict) and b.get("type") == "text" and b.get("id") == el["id"] for b in bound):
            bound.append({"type": "text", "id": el["id"]})
        container["boundElements"] = bound

    if label_override:
        _rewrite_library_label(builder, copied, label_override)

    return copied


class ExcalidrawBuilder:
    """
    A tiny Excalidraw scene builder that enforces BMAD-style invariants:
    - snap coordinates to grid
    - bind text to container shapes (containerId + groupIds + boundElements)
    """

    def __init__(self, *, rng: random.Random, grid: int, theme: Theme, fidelity: Fidelity):
        self._rng = rng
        self._grid = max(1, int(grid))
        self._theme = theme
        self._fidelity = fidelity

    @property
    def grid(self) -> int:
        return self._grid

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def fidelity(self) -> Fidelity:
        return self._fidelity

    def _seed(self) -> int:
        return self._rng.randint(1, 2**31 - 1)

    def snap(self, value: float) -> int:
        return _round_to(value, self._grid)

    def _shape_style(self) -> dict[str, Any]:
        # Keep wireframes readable and consistent.
        if self._fidelity == "low":
            return {
                "strokeColor": self._theme.border,
                "backgroundColor": "transparent",
                "fillStyle": "hachure",
                "strokeWidth": 1,
                "roughness": 1,
            }
        if self._fidelity == "high":
            return {
                "strokeColor": self._theme.border,
                "backgroundColor": self._theme.container,
                "fillStyle": "solid",
                "strokeWidth": 2,
                "roughness": 0,
            }
        return {
            "strokeColor": self._theme.border,
            "backgroundColor": self._theme.container,
            "fillStyle": "solid",
            "strokeWidth": 2,
            "roughness": 0,
        }

    def rect(
        self,
        *,
        x: float,
        y: float,
        w: float,
        h: float,
        roundness: int | None = 4,
        seer_label: str | None = None,
        custom_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        style = self._shape_style()
        el: dict[str, Any] = {
            "id": _new_id(),
            "type": "rectangle",
            "x": float(self.snap(x)),
            "y": float(self.snap(y)),
            "width": float(self.snap(w)),
            "height": float(self.snap(h)),
            "angle": 0,
            "strokeColor": style["strokeColor"],
            "backgroundColor": style["backgroundColor"],
            "fillStyle": style["fillStyle"],
            "strokeWidth": style["strokeWidth"],
            "strokeStyle": "solid",
            "roughness": style["roughness"],
            "opacity": 100,
            "groupIds": [],
            "roundness": {"type": 3, "value": int(roundness)} if roundness is not None else None,
            "seed": self._seed(),
            "version": 1,
            "versionNonce": self._seed(),
            "isDeleted": False,
            "boundElements": None,
            "updated": _now_ms(),
            "link": None,
            "locked": False,
        }
        if seer_label:
            el["customData"] = {"seerLabel": seer_label}
        if custom_data:
            el.setdefault("customData", {})
            el["customData"].update(custom_data)
        return el

    def text(
        self,
        *,
        x: float,
        y: float,
        text: str,
        font_size: int = 16,
        color: str | None = None,
        align: Literal["left", "center", "right"] = "left",
        valign: Literal["top", "middle", "bottom"] = "top",
        container_id: str | None = None,
        group_ids: list[str] | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        color = color or self._theme.text
        if width is None or height is None:
            w0, h0 = _text_size(text, font_size)
            width = width or w0
            height = height or h0
        return {
            "id": _new_id(),
            "type": "text",
            "x": float(self.snap(x)),
            "y": float(self.snap(y)),
            "width": float(_round_to(width, 10)),
            "height": float(_round_to(height, 10)),
            "angle": 0,
            "strokeColor": color,
            "backgroundColor": "transparent",
            "fillStyle": "hachure",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": group_ids or [],
            "roundness": None,
            "seed": self._seed(),
            "version": 1,
            "versionNonce": self._seed(),
            "isDeleted": False,
            "boundElements": None,
            "updated": _now_ms(),
            "link": None,
            "locked": False,
            "text": text,
            "fontSize": int(font_size),
            "fontFamily": 1,  # 1 = Virgil
            "textAlign": align,
            "verticalAlign": valign,
            "baseline": int(font_size * 1.2),
            "containerId": container_id,
            "originalText": text,
            "lineHeight": 1.25,
        }

    def line(self, *, x: float, y: float, x2: float, y2: float) -> dict[str, Any]:
        x0 = self.snap(x)
        y0 = self.snap(y)
        x1 = self.snap(x2)
        y1 = self.snap(y2)
        return {
            "id": _new_id(),
            "type": "line",
            "x": float(x0),
            "y": float(y0),
            "width": float(x1 - x0),
            "height": float(y1 - y0),
            "angle": 0,
            "strokeColor": self._theme.border,
            "backgroundColor": "transparent",
            "fillStyle": "hachure",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "groupIds": [],
            "roundness": None,
            "seed": self._seed(),
            "version": 1,
            "versionNonce": self._seed(),
            "isDeleted": False,
            "boundElements": None,
            "updated": _now_ms(),
            "link": None,
            "locked": False,
            "points": [[0, 0], [float(x1 - x0), float(y1 - y0)]],
            "lastCommittedPoint": None,
            "startBinding": None,
            "endBinding": None,
            "startArrowhead": None,
            "endArrowhead": None,
        }

    def labeled_rect(
        self,
        *,
        x: float,
        y: float,
        w: float,
        h: float,
        text: str,
        font_size: int = 16,
        label_color: str | None = None,
        roundness: int | None = 4,
        seer_label: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # BMAD invariants: shared groupIds; text containerId; shape boundElements.
        group_id = _new_id()
        rect = self.rect(x=x, y=y, w=w, h=h, roundness=roundness, seer_label=seer_label)
        rect["groupIds"] = [group_id]

        label_color = label_color or self._theme.text
        label_width = _calc_label_width(text, font_size)
        label_height = _round_to(font_size * 1.6, 10)

        rx = float(rect["x"])
        ry = float(rect["y"])
        rw = float(rect["width"])
        rh = float(rect["height"])

        tx = rx + (rw - label_width) / 2
        ty = ry + (rh - label_height) / 2

        txt = self.text(
            x=tx,
            y=ty,
            text=text,
            font_size=font_size,
            color=label_color,
            align="center",
            valign="middle",
            container_id=rect["id"],
            group_ids=[group_id],
            width=label_width,
            height=label_height,
        )
        rect["boundElements"] = [{"type": "text", "id": txt["id"]}]
        return rect, txt


def _fit_group_to_bounds(
    builder: ExcalidrawBuilder,
    group: list[dict[str, Any]],
    *,
    max_w: float | None,
    max_h: float | None,
) -> float:
    if not group:
        return 1.0
    gx0, gy0, gx1, gy1 = _bbox_for_elements(group)
    w = float(gx1 - gx0)
    h = float(gy1 - gy0)
    if w <= 0 or h <= 0:
        return 1.0

    scale = 1.0
    if max_w is not None and w > max_w:
        scale = min(scale, float(max_w) / w)
    if max_h is not None and h > max_h:
        scale = min(scale, float(max_h) / h)

    if scale >= 0.999:
        return 1.0

    def sx(v: float) -> float:
        return gx0 + (v - gx0) * scale

    def sy(v: float) -> float:
        return gy0 + (v - gy0) * scale

    for el in group:
        if "x" in el:
            try:
                el["x"] = float(_round_to(sx(float(el["x"])), 1))
            except Exception:
                pass
        if "y" in el:
            try:
                el["y"] = float(_round_to(sy(float(el["y"])), 1))
            except Exception:
                pass
        for k in ("width", "height"):
            if k in el and el[k] is not None:
                try:
                    el[k] = float(_round_to(float(el[k]) * scale, 1))
                except Exception:
                    pass
        if isinstance(el.get("points"), list):
            pts = []
            for p in el["points"]:
                if not isinstance(p, list) or len(p) < 2:
                    pts.append(p)
                    continue
                try:
                    pts.append([float(_round_to(float(p[0]) * scale, 1)), float(_round_to(float(p[1]) * scale, 1))])
                except Exception:
                    pts.append(p)
            el["points"] = pts
        if el.get("type") == "text":
            for k in ("fontSize", "baseline"):
                if k in el and el[k] is not None:
                    try:
                        el[k] = float(el[k]) * scale
                    except Exception:
                        pass
        if "strokeWidth" in el and el["strokeWidth"] is not None:
            try:
                el["strokeWidth"] = max(1, float(el["strokeWidth"]) * scale)
            except Exception:
                pass

    return scale


def _offset_group(group: list[dict[str, Any]], *, dx: float = 0.0, dy: float = 0.0) -> None:
    if not group or (dx == 0 and dy == 0):
        return
    for el in group:
        if "x" in el:
            try:
                el["x"] = float(_round_to(float(el["x"]) + dx, 1))
            except Exception:
                pass
        if "y" in el:
            try:
                el["y"] = float(_round_to(float(el["y"]) + dy, 1))
            except Exception:
                pass


def _simplify_library_header(group: list[dict[str, Any]], *, keep_actions: bool) -> None:
    if not group or keep_actions:
        return
    gx0, _, gx1, _ = _bbox_for_elements(group)
    cutoff = gx0 + (gx1 - gx0) * 0.65
    to_remove: list[dict[str, Any]] = []
    for el in group:
        if el.get("type") not in ("line", "ellipse", "diamond", "rectangle"):
            continue
        try:
            if float(el.get("x") or 0) > cutoff:
                to_remove.append(el)
        except Exception:
            continue
    if to_remove:
        for el in to_remove:
            if el in group:
                group.remove(el)


def _apply_theme_to_library_group(builder: ExcalidrawBuilder, group: list[dict[str, Any]]) -> None:
    if not group:
        return
    stroke = builder.theme.border
    text_color = builder.theme.text
    stroke_width = builder._shape_style().get("strokeWidth", 2)
    group_label = None
    for el in group:
        custom = el.get("customData")
        if isinstance(custom, dict) and custom.get("seerLabel"):
            group_label = custom.get("seerLabel")
            break
    is_button = group_label == "button"
    for el in group:
        etype = el.get("type")
        label = None
        custom = el.get("customData")
        if isinstance(custom, dict):
            label = custom.get("seerLabel")
        if etype == "text":
            if is_button:
                el["strokeColor"] = "#ffffff"
            else:
                el["strokeColor"] = text_color
            continue
        if etype in ("rectangle", "ellipse", "diamond", "line", "arrow"):
            el["strokeColor"] = stroke
            if "strokeWidth" in el and el["strokeWidth"] is not None:
                el["strokeWidth"] = stroke_width
            if is_button and etype == "rectangle":
                if not el.get("backgroundColor") or el.get("backgroundColor") == "transparent":
                    el["backgroundColor"] = "#1e1e1e"
                el["fillStyle"] = "solid"
            if etype == "rectangle" and label in ("card", "image"):
                el["backgroundColor"] = builder.theme.container
                el["fillStyle"] = "solid"
                el["roundness"] = {"type": 3, "value": 8}

def _normalize_input_group(
    builder: ExcalidrawBuilder,
    group: list[dict[str, Any]],
    *,
    x: float,
    y: float,
    w: float,
    h: float,
) -> None:
    if not group:
        return
    rect = None
    for el in group:
        if el.get("type") == "rectangle" and el.get("boundElements"):
            rect = el
            break
    if not rect:
        return
    rect["x"] = float(_round_to(x, 1))
    rect["y"] = float(_round_to(y, 1))
    rect["width"] = float(_round_to(w, 1))
    rect["height"] = float(_round_to(h, 1))

    # Align placeholder text inside the new bounds.
    by_id = {el.get("id"): el for el in group if isinstance(el.get("id"), str)}
    for b in rect.get("boundElements") or []:
        if not isinstance(b, dict):
            continue
        tid = b.get("id")
        if tid not in by_id:
            continue
        text_el = by_id[tid]
        new_text = str(text_el.get("text") or "").strip()
        if not new_text:
            continue
        try:
            font_size = int(float(text_el.get("fontSize") or 14))
        except Exception:
            font_size = 14
        new_w = min(int(w - builder.grid * 2), _calc_label_width(new_text, font_size))
        new_w = max(24, new_w)
        text_el["width"] = float(_round_to(new_w, 10))
        text_el["height"] = float(_round_to(max(float(text_el.get("height") or 0), font_size * 1.6), 10))
        text_el["x"] = float(_round_to(x + builder.grid, 1))
        text_el["y"] = float(_round_to(y + (h - float(text_el["height"])) / 2, 1))


def _normalize_card_group(
    builder: ExcalidrawBuilder,
    group: list[dict[str, Any]],
    *,
    x: float,
    y: float,
    w: float,
    h: float,
) -> None:
    if not group:
        return
    rect = None
    for el in group:
        if el.get("type") == "rectangle" and el.get("boundElements"):
            rect = el
            break
    if not rect:
        return
    rect["x"] = float(_round_to(x, 1))
    rect["y"] = float(_round_to(y, 1))
    rect["width"] = float(_round_to(w, 1))
    rect["height"] = float(_round_to(h, 1))
    # Re-center label text if present.
    by_id = {el.get("id"): el for el in group if isinstance(el.get("id"), str)}
    for b in rect.get("boundElements") or []:
        if not isinstance(b, dict):
            continue
        tid = b.get("id")
        if tid not in by_id:
            continue
        text_el = by_id[tid]
        _set_library_text(builder, group, text_el, str(text_el.get("text") or ""))


def _normalize_header_group(
    builder: ExcalidrawBuilder,
    group: list[dict[str, Any]],
    *,
    x: float,
    y: float,
    w: float,
    h: float,
) -> None:
    if not group:
        return
    rect = None
    for el in group:
        if el.get("type") != "rectangle":
            continue
        custom = el.get("customData")
        if isinstance(custom, dict) and custom.get("seerLabel") == "header":
            rect = el
            break
        if rect is None:
            rect = el
    if not rect:
        return
    rect["x"] = float(_round_to(x, 1))
    rect["y"] = float(_round_to(y, 1))
    rect["width"] = float(_round_to(w, 1))
    rect["height"] = float(_round_to(h, 1))

    texts = [el for el in group if el.get("type") == "text" and isinstance(el.get("text"), str)]
    if not texts:
        return
    target = sorted(texts, key=lambda el: (float(el.get("y") or 0), float(el.get("x") or 0)))[0]
    new_text = str(target.get("text") or "").strip()
    if not new_text:
        return
    try:
        font_size = int(float(target.get("fontSize") or 16))
    except Exception:
        font_size = 16
    new_w = min(int(w - builder.grid * 2), _calc_label_width(new_text, font_size))
    new_w = max(24, new_w)
    target["width"] = float(_round_to(new_w, 10))
    target["height"] = float(_round_to(max(float(target.get("height") or 0), font_size * 1.6), 10))
    target["textAlign"] = "left"
    target["x"] = float(_round_to(x + builder.grid, 1))
    target["y"] = float(_round_to(y + (h - float(target["height"])) / 2, 1))

    icon_elems = [el for el in group if el.get("type") in ("line", "ellipse", "diamond", "arrow")]
    if icon_elems:
        ix0, iy0, ix1, iy1 = _bbox_for_elements(icon_elems)
        icon_center = (iy0 + iy1) / 2
        rect_center = float(rect.get("y") or y) + float(rect.get("height") or h) / 2
        dy = rect_center - icon_center
        if abs(dy) >= 1:
            _offset_group(icon_elems, dy=dy)
        # Add a little left padding for leading icons (e.g., hamburger).
        left_cluster = [el for el in icon_elems if float(el.get("x") or 0) <= float(rect["x"]) + float(rect["width"]) * 0.4]
        if left_cluster:
            lx0, _, lx1, _ = _bbox_for_elements(left_cluster)
            desired_left = float(rect["x"]) + builder.grid / 2
            dx = desired_left - lx0
            if abs(dx) >= 1:
                _offset_group(left_cluster, dx=dx)
            min_text_x = lx1 + builder.grid * 0.5 + dx
            if float(target.get("x") or 0) < min_text_x:
                target["x"] = float(_round_to(min_text_x, 1))


def _set_rect_bounds(rect: dict[str, Any], *, x: float, y: float, w: float, h: float) -> None:
    rect["x"] = float(_round_to(x, 1))
    rect["y"] = float(_round_to(y, 1))
    rect["width"] = float(_round_to(w, 1))
    rect["height"] = float(_round_to(h, 1))

def _split_items(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    quoted: list[str] = []

    def _q(m: re.Match[str]) -> str:
        quoted.append(m.group(0)[1:-1])
        return f"__Q{len(quoted)-1}__"

    tmp = re.sub(r'"[^"]+"|\'[^\']+\'', _q, text)
    parts = [p.strip() for p in re.split(r"[,\n]+", tmp) if p.strip()]
    out: list[str] = []
    for p in parts:
        out.append(re.sub(r"__Q(\d+)__", lambda m: quoted[int(m.group(1))], p).strip())
    return out


def _infer_preset(text: str) -> CanvasPreset:
    lowered = text.lower()
    for key, preset in PRESETS.items():
        if re.search(rf"\b{re.escape(key)}\b", lowered):
            return preset
    if "iphone" in lowered or "ios" in lowered or "android" in lowered or "mobile" in lowered:
        return PRESETS["mobile"]
    if "desktop" in lowered or "web" in lowered:
        return PRESETS["desktop"]
    return PRESETS["mobile"]


def _infer_size(text: str) -> tuple[int, int] | None:
    m = re.search(r"(\d{2,5})\s*[x×]\s*(\d{2,5})", text)
    if not m:
        return None
    w = int(m.group(1))
    h = int(m.group(2))
    if w < 100 or h < 100:
        return None
    return w, h


def _iter_phrases(text: str) -> Iterable[str]:
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(raw_lines) >= 2:
        for ln in raw_lines:
            yield re.sub(r"^[-*•]\s*", "", ln).strip()
        return

    parts = [p.strip() for p in re.split(r"[;|]+", text) if p.strip()]
    if len(parts) == 1:
        parts = [p.strip() for p in re.split(r"\.\s+", text) if p.strip()]
    for p in parts:
        yield p


@dataclass(frozen=True)
class ScreenSpec:
    name: str
    phrases: list[str]


def _parse_component(phrase: str) -> tuple[str, str]:
    m = re.match(r"(?i)^\s*(" + "|".join(map(re.escape, COMPONENT_TYPES)) + r")\s*[:\-]\s*(.*)$", phrase)
    if m:
        return m.group(1).lower(), m.group(2).strip()

    lowered = phrase.lower()
    if lowered.startswith("add ") or lowered.startswith("create "):
        phrase = re.sub(r"(?i)^\s*(add|create)\s+", "", phrase).strip()
        return "text", phrase

    if "button" in lowered:
        label = re.sub(r"(?i)\bbutton\b", "", phrase).strip(" :,-")
        return "button", label or phrase

    return "text", phrase


def _group_screens(phrases: list[str]) -> list[ScreenSpec]:
    screens: list[ScreenSpec] = []
    current_name: str | None = None
    current: list[str] = []

    for phrase in phrases:
        comp_type, value = _parse_component(phrase)
        if comp_type == "screen":
            if current_name is not None or current:
                screens.append(ScreenSpec(name=current_name or f"Screen {len(screens)+1}", phrases=current))
            current_name = value.strip() or f"Screen {len(screens)+1}"
            current = []
            continue
        current.append(phrase)

    if current_name is not None or current:
        screens.append(ScreenSpec(name=current_name or f"Screen {len(screens)+1}", phrases=current))

    if not screens:
        return [ScreenSpec(name="Screen", phrases=phrases)]
    return screens


def _layout_screen(
    *,
    builder: ExcalidrawBuilder,
    screen_x: int,
    screen_y: int,
    screen_w: int,
    screen_h: int,
    screen_name: str,
    phrases: list[str],
    library: ExcalidrawLibrary | None,
    prefer_library: bool,
    show_label: bool,
) -> list[dict[str, Any]]:
    g = builder.grid
    margin = g
    gap = g
    pad = g

    elements: list[dict[str, Any]] = []

    # Screen boundary. Keep boundary transparent to avoid overwhelming the page.
    boundary = builder.rect(
        x=screen_x,
        y=screen_y,
        w=screen_w,
        h=screen_h,
        roundness=8,
        seer_label="screen",
    )
    boundary["backgroundColor"] = "transparent"
    boundary["fillStyle"] = "hachure"
    elements.append(boundary)
    if show_label:
        label_y = max(0, screen_y - builder.grid)
        elements.append(builder.text(x=screen_x, y=label_y, text=screen_name, font_size=12, color=builder.theme.muted_text))

    content_x = screen_x + margin
    content_w = screen_w - margin * 2
    y = screen_y + margin

    for phrase in phrases:
        comp_type, value = _parse_component(phrase)
        if comp_type == "screen":
            continue

        if comp_type == "divider":
            elements.append(builder.line(x=content_x, y=y, x2=content_x + content_w, y2=y))
            y += gap // 2
            continue

        if comp_type == "section":
            label = value or "Section"
            used = False
            if prefer_library and library:
                item = library.find("section title")
                if item:
                    group = instantiate_library_item(
                        builder=builder,
                        item=item,
                        x=content_x,
                        y=y,
                        label_override=None,
                        seer_label="section",
                    )
                    _fit_group_to_bounds(builder, group, max_w=content_w, max_h=28)
                    _rewrite_library_section_title(builder, group, label)
                    elements.extend(group)
                    _, _, _, by1 = _bbox_for_elements(group)
                    y = builder.snap(by1 + builder.grid)
                    used = True
            if not used:
                elements.append(builder.text(x=content_x + pad, y=y, text=label.upper(), font_size=12, color=builder.theme.muted_text))
                y = builder.snap(y + builder.grid)
            elements.append(builder.line(x=content_x, y=y, x2=content_x + content_w, y2=y))
            y = builder.snap(y + builder.grid)
            continue

        if comp_type == "text":
            label = value or phrase
            elements.append(builder.text(x=content_x, y=y, text=label, font_size=16, color=builder.theme.text))
            y += 32
            continue

        if comp_type == "chips":
            items = _split_items(value or "")
            if prefer_library and library:
                chip = library.find("chips")
                if chip and items:
                    x_cursor = content_x
                    y0 = y
                    max_y1 = y
                    for it_label in items[:8]:
                        group = instantiate_library_item(
                            builder=builder,
                            item=chip,
                            x=x_cursor,
                            y=y0,
                            label_override=it_label,
                            seer_label="chips",
                        )
                        elements.extend(group)
                        gx0, gy0, gx1, gy1 = _bbox_for_elements(group)
                        x_cursor = builder.snap(gx1 + builder.grid)
                        max_y1 = max(max_y1, gy1)
                    y = builder.snap(max_y1 + gap)
                    continue
            # Fallback: simple inline list
            elements.append(builder.text(x=content_x, y=y, text=", ".join(items) or "chips", font_size=16, color=builder.theme.muted_text))
            y += 32
            continue

        if comp_type in ("lib", "library"):
            # Syntax:
            #   lib: <Library Item Name>
            #   lib: <Library Item Name> | <label override>
            parts = [p.strip() for p in value.split("|", 1)]
            item_name = parts[0] if parts else ""
            label_override = parts[1] if len(parts) > 1 else None
            if library and item_name:
                item = library.find(item_name)
                if item:
                    group = instantiate_library_item(
                        builder=builder,
                        item=item,
                        x=content_x,
                        y=y,
                        label_override=label_override,
                        seer_label="lib",
                    )
                    elements.extend(group)
                    _, _, _, by1 = _bbox_for_elements(group)
                    y = builder.snap(by1 + gap)
                    continue
            # Fallback: just render the request as text.
            elements.append(builder.text(x=content_x, y=y, text=f"lib: {value}", font_size=16, color=builder.theme.muted_text))
            y += 32
            continue

        height_by_type = {
            "header": 60,
            "tabs": 48,
            "input": 52,
            "button": 52,
            "dropdown": 52,
            "textarea": 120,
            "checkbox": 40,
            "radio": 40,
            "toggle": 40,
            "card": 140,
            "list": 160,
            "image": 160,
            "footer": 80,
        }
        h = height_by_type.get(comp_type, 80)
        label = (value or comp_type.title()).strip()

        if prefer_library and library and comp_type in DEFAULT_LIBRARY_COMPONENT_QUERIES:
            item = _pick_library_item_for_component(library, comp_type, label)
            if item:
                x0, y0, x1, y1 = _bbox_for_elements(item.elements)
                item_w = x1 - x0
                item_h = y1 - y0
                place_x = content_x
                if comp_type == "button" and item_w > 0 and item_w < content_w:
                    place_x = int(content_x + (content_w - item_w) / 2)
                # Only override labels when we can do so reliably without distorting the library item.
                label_override = None
                group = instantiate_library_item(
                    builder=builder,
                    item=item,
                    x=place_x,
                    y=y,
                    label_override=label_override,
                    seer_label=comp_type,
                )
                # Fit library components into the screen content width/height to avoid overlap across screens.
                max_w = content_w
                max_h = None
                if comp_type in ("header", "footer", "tabs"):
                    max_h = h
                elif comp_type in ("card", "image"):
                    max_h = h
                elif comp_type in ("input", "dropdown"):
                    max_h = h
                elif comp_type == "textarea":
                    max_h = h
                _fit_group_to_bounds(builder, group, max_w=max_w, max_h=max_h)

                if comp_type == "header":
                    _simplify_library_header(group, keep_actions=False)
                    # If the library header has no background, add one for clarity and spacing.
                    has_rect = any(el.get("type") == "rectangle" for el in group)
                    if not has_rect:
                        header_bg = builder.rect(
                            x=content_x,
                            y=y,
                            w=content_w,
                            h=h,
                            roundness=8,
                            seer_label="header",
                        )
                        _set_rect_bounds(header_bg, x=content_x, y=y, w=content_w, h=h)
                        group.insert(0, header_bg)

                if max_h:
                    gx0, gy0, gx1, gy1 = _bbox_for_elements(group)
                    group_h = gy1 - gy0
                    if group_h > 0 and max_h > group_h:
                        _offset_group(group, dy=(max_h - group_h) / 2)

                _apply_theme_to_library_group(builder, group)

                if comp_type == "input":
                    _normalize_input_group(builder, group, x=content_x, y=y, w=content_w, h=h)

                if comp_type == "card":
                    _normalize_card_group(builder, group, x=content_x, y=y, w=content_w, h=h)

                if comp_type == "header":
                    _normalize_header_group(builder, group, x=content_x, y=y, w=content_w, h=h)

                if comp_type in ("button", "tabs", "footer"):
                    gx0, _, gx1, _ = _bbox_for_elements(group)
                    group_w = gx1 - gx0
                    if group_w > 0 and group_w < content_w:
                        _offset_group(group, dx=(content_w - group_w) / 2)

                if comp_type == "tabs":
                    tab_labels = [t.strip() for t in re.split(r"\s*\|\s*", label) if t.strip()]
                    if len(tab_labels) <= 1:
                        tab_labels = _split_items(label) or [label or "Tab"]
                    _rewrite_library_tabs_labels(builder, group, tab_labels)
                elif comp_type in ("button", "header", "card"):
                    if label:
                        _rewrite_library_label(builder, group, label)
                elif comp_type == "footer":
                    footer_labels = [t.strip() for t in re.split(r"\s*\|\s*", label) if t.strip()]
                    if len(footer_labels) <= 1:
                        footer_labels = _split_items(label) or []
                    if footer_labels:
                        _rewrite_library_footer_labels(builder, group, footer_labels)
                elif comp_type == "input":
                    _rewrite_library_label_and_placeholder(builder, group, label="", placeholder=label or "Input")
                elif comp_type in ("dropdown", "textarea"):
                    _rewrite_library_label_and_placeholder(
                        builder,
                        group,
                        label=label,
                        placeholder="Select…" if comp_type == "dropdown" else "Enter text…",
                    )
                elif comp_type in ("checkbox", "radio", "toggle"):
                    # Strip simple state hints from the label.
                    cleaned = re.sub(r"\((on|off|true|false|enabled|disabled|checked|unchecked)\)", "", label, flags=re.I).strip()
                    cleaned = re.sub(r"\b(on|off|true|false|enabled|disabled|checked|unchecked)\b", "", cleaned, flags=re.I).strip()
                    if cleaned:
                        _rewrite_library_label(builder, group, cleaned)
                elements.extend(group)
                gx0, gy0, gx1, gy1 = _bbox_for_elements(group)
                y = builder.snap(gy1 + gap)
                continue

        if comp_type == "header":
            rect, txt = builder.labeled_rect(x=content_x, y=y, w=content_w, h=h, text=label, font_size=18, roundness=8, seer_label="header")
            elements.extend([rect, txt])
        elif comp_type == "button":
            rect, txt = builder.labeled_rect(x=content_x, y=y, w=min(content_w, 320), h=h, text=label, font_size=18, roundness=6, seer_label="button")
            elements.extend([rect, txt])
        elif comp_type == "tabs":
            # Render segmented tabs: `tabs: A | B | C`
            tab_labels = [t.strip() for t in re.split(r"\s*\|\s*", label) if t.strip()]
            if len(tab_labels) <= 1:
                tab_labels = _split_items(label) or [label or "Tab"]
            rect = builder.rect(x=content_x, y=y, w=content_w, h=h, roundness=6, seer_label="tabs")
            rect["backgroundColor"] = "transparent"
            rect["fillStyle"] = "hachure"
            elements.append(rect)
            n = max(1, min(6, len(tab_labels)))
            seg_w = float(content_w) / n
            for i in range(n):
                if i > 0:
                    x_sep = content_x + seg_w * i
                    elements.append(builder.line(x=x_sep, y=y, x2=x_sep, y2=y + h))
                t = tab_labels[i]
                label_w = _calc_label_width(t, 14)
                label_h = _round_to(14 * 1.6, 10)
                tx = content_x + seg_w * i + (seg_w - label_w) / 2
                ty = y + (h - label_h) / 2
                elements.append(builder.text(x=tx, y=ty, text=t, font_size=14, color=builder.theme.text, align="center", valign="middle", width=label_w, height=label_h))
        elif comp_type == "input":
            rect, txt = builder.labeled_rect(
                x=content_x, y=y, w=min(content_w, 520), h=h, text=label or "Input", font_size=16, roundness=6, seer_label="input"
            )
            txt["strokeColor"] = builder.theme.muted_text
            elements.extend([rect, txt])
        elif comp_type == "image":
            rect = builder.rect(x=content_x, y=y, w=content_w, h=h, roundness=8, seer_label="image")
            rect["fillStyle"] = "cross-hatch"
            elements.append(rect)
            elements.append(builder.text(x=content_x + pad, y=y + pad, text=label or "Image", font_size=16, color=builder.theme.muted_text))
        elif comp_type == "list":
            items = _split_items(label)
            row_h = max(builder.grid * 2, 32)
            list_h = builder.grid * 2 + row_h * max(1, len(items))
            rect = builder.rect(x=content_x, y=y, w=content_w, h=list_h, roundness=8, seer_label="list")
            _set_rect_bounds(rect, x=content_x, y=y, w=content_w, h=list_h)
            elements.append(rect)
            row_y = y + builder.grid
            for idx, item in enumerate(items[:7]):
                if idx > 0:
                    elements.append(builder.line(x=content_x + 8, y=row_y, x2=content_x + content_w - 8, y2=row_y))
                font_size = 14
                t_w, t_h = _text_size(item, font_size)
                baseline = int(font_size * 1.2)
                text_y = row_y + (row_h / 2) - baseline
                elements.append(
                    builder.text(
                        x=content_x + pad,
                        y=text_y,
                        text=item,
                        font_size=font_size,
                        color=builder.theme.text,
                        width=t_w,
                        height=t_h,
                    )
                )
                row_y += row_h
            h = list_h
        else:
            rect, txt = builder.labeled_rect(x=content_x, y=y, w=content_w, h=h, text=label, font_size=16, roundness=8, seer_label=comp_type)
            elements.extend([rect, txt])

        y += h + gap
        if y > screen_y + screen_h - margin:
            elements.append(builder.text(x=content_x, y=screen_y + screen_h - margin, text="(more omitted…)", font_size=14, color=builder.theme.muted_text))
            break

    return elements


def _validate_scene(scene: dict[str, Any], *, grid: int) -> None:
    elements = scene.get("elements") or []
    by_id: dict[str, dict[str, Any]] = {}
    for el in elements:
        elid = el.get("id")
        if not isinstance(elid, str) or not elid:
            raise ValueError("element missing id")
        if elid in by_id:
            raise ValueError(f"duplicate element id: {elid}")
        if el.get("isDeleted") is True:
            raise ValueError(f"isDeleted element present: {elid}")
        by_id[elid] = el

    # Container/text binding invariants.
    for el in elements:
        if el.get("type") != "text":
            continue
        container_id = el.get("containerId")
        if not container_id:
            continue
        container = by_id.get(container_id)
        if not container:
            raise ValueError(f"text {el['id']} references missing containerId {container_id}")
        if (el.get("groupIds") or []) != (container.get("groupIds") or []):
            raise ValueError(f"text/container groupIds mismatch for {el['id']} -> {container_id}")
        bound = container.get("boundElements") or []
        if not any((b.get("type") == "text" and b.get("id") == el["id"]) for b in bound if isinstance(b, dict)):
            raise ValueError(f"container {container_id} missing boundElements reference to text {el['id']}")

    def _is_on_grid(value: Any) -> bool:
        try:
            v = float(value)
        except Exception:
            return True
        return abs(v - round(v / grid) * grid) < 1e-6

    for el in elements:
        custom = el.get("customData")
        if isinstance(custom, dict) and custom.get("seerSource") == "library":
            continue
        for key in ("x", "y"):
            if key in el and not _is_on_grid(el[key]):
                raise ValueError(f"element {el['id']} not snapped to grid ({key}={el[key]})")


def build_scene(
    *,
    text: str,
    preset: CanvasPreset,
    size: tuple[int, int] | None,
    theme: Theme,
    fidelity: Fidelity,
    seed: int | None,
    strict: bool,
    library: ExcalidrawLibrary | None,
    prefer_library: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    global _ID_RNG, _NOW_MS
    rng = random.Random(seed)
    _ID_RNG = random.Random((seed or 0) ^ 0x5EED5EED)
    _NOW_MS = 1_700_000_000_000 + (seed or 0)
    builder = ExcalidrawBuilder(rng=rng, grid=preset.grid_size, theme=theme, fidelity=fidelity)

    if size:
        screen_w, screen_h = size
    else:
        screen_w, screen_h = preset.width, preset.height

    phrases = [p for p in _iter_phrases(text)]
    screens = _group_screens(phrases)

    g = builder.grid
    outer = g * 2
    hgap = g * 4

    canvas_w = outer * 2 + (screen_w * len(screens)) + (hgap * max(0, len(screens) - 1))
    canvas_h = outer * 2 + screen_h

    elements: list[dict[str, Any]] = []
    show_label = False
    for idx, screen in enumerate(screens):
        sx = builder.snap(outer + idx * (screen_w + hgap))
        sy = outer
        elements.extend(
            _layout_screen(
                builder=builder,
                screen_x=sx,
                screen_y=sy,
                screen_w=screen_w,
                screen_h=screen_h,
                screen_name=screen.name,
                phrases=screen.phrases,
                library=library,
                prefer_library=prefer_library,
                show_label=show_label,
            )
        )

    scene = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {
            "gridSize": preset.grid_size,
            "viewBackgroundColor": theme.background,
            # Slightly zoom out when multiple screens are present to improve initial framing.
            "zoom": {"value": 0.8 if len(screens) > 1 else 1},
            "scrollX": 0,
            "scrollY": 0,
        },
        "files": {},
    }

    if strict:
        _validate_scene(scene, grid=preset.grid_size)

    library_used_total = 0
    library_used_by_label: dict[str, int] = {}
    for el in elements:
        custom = el.get("customData")
        if not isinstance(custom, dict):
            continue
        if custom.get("seerSource") != "library":
            continue
        library_used_total += 1
        label = str(custom.get("seerLabel") or "unknown")
        library_used_by_label[label] = library_used_by_label.get(label, 0) + 1

    meta = {
        "preset": preset.name,
        "theme": theme.name,
        "fidelity": fidelity,
        "grid": preset.grid_size,
        "library_used": {
            "loaded": bool(library),
            "prefer_library": bool(prefer_library),
            "elements_total": library_used_total,
            "by_component": library_used_by_label,
        },
        "screens": [{"name": s.name, "count_phrases": len(s.phrases)} for s in screens],
        "layout": {
            "canvas_width": canvas_w,
            "canvas_height": canvas_h,
            "screen_width": screen_w,
            "screen_height": screen_h,
        },
    }
    _ID_RNG = None
    _NOW_MS = None
    return scene, meta


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate a .excalidraw file from natural-language-ish text.")
    parser.add_argument("--text", help="Prompt text. If omitted, reads stdin.")
    parser.add_argument("--spec", help="Path to a text file prompt (alternative to --text).")
    parser.add_argument("--preset", choices=sorted(PRESETS.keys()), help="Canvas preset. If omitted, inferred from text.")
    parser.add_argument("--size", help='Override canvas size, e.g. "390x844".')
    parser.add_argument("--theme", choices=sorted(THEMES.keys()), default="classic", help="Color theme.")
    parser.add_argument("--fidelity", choices=["low", "medium", "high"], default="medium", help="Wireframe fidelity level.")
    parser.add_argument("--seed", type=int, help="Deterministic seed for reproducible outputs.")
    parser.add_argument(
        "--library",
        help="Path to a .excalidrawlib file. Defaults to the bundled basic UX library if present.",
    )
    parser.add_argument("--no-library", action="store_true", help="Disable Excalidraw library usage.")
    parser.add_argument("--no-strict", action="store_true", help="Disable invariant validation (not recommended).")
    parser.add_argument("--name", default="wireframe", help="Slug used for default output name.")
    parser.add_argument("--out", help="Output .excalidraw path. If omitted, writes under .seer/excalidraw/.")
    parser.add_argument("--json", action="store_true", help="Print metadata JSON to stdout (suppresses path output).")
    args = parser.parse_args(argv)

    if args.text and args.spec:
        print("error: pass only one of --text or --spec", file=sys.stderr)
        return 2

    if args.spec:
        try:
            text = open(args.spec, "r", encoding="utf-8").read()
        except Exception as e:
            print(f"error: failed to read --spec: {e}", file=sys.stderr)
            return 2
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    text = (text or "").strip()
    if not text:
        print("error: empty input text. Provide --text or pipe text via stdin.", file=sys.stderr)
        return 2

    preset = PRESETS.get(args.preset) if args.preset else _infer_preset(text)

    size = None
    if args.size:
        size = _infer_size(args.size)
        if not size:
            print(f"error: invalid --size: {args.size}", file=sys.stderr)
            return 2
    else:
        size = _infer_size(text)

    out_root = os.environ.get("SEER_OUT_DIR") or os.environ.get("SEER_TMP_DIR") or ".seer"
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_id = f"{ts}-{os.getpid()}-{random.randint(0, 99999)}"
    slug = _slugify(args.name)

    out_path = args.out or os.path.join(_default_excalidraw_output_dir(out_root), f"nl-{slug}-{run_id}.excalidraw")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    theme = THEMES[args.theme]
    fidelity: Fidelity = args.fidelity  # type: ignore[assignment]
    strict = not args.no_strict

    seed = args.seed
    if seed is None:
        size_label = f"{size[0]}x{size[1]}" if size else ""
        seed = _stable_seed(
            text,
            preset.name,
            size_label,
            theme.name,
            fidelity,
        )

    library: ExcalidrawLibrary | None = None
    prefer_library = False
    if not args.no_library:
        lib_path = Path(args.library) if args.library else _default_library_path()
        if lib_path.exists():
            try:
                library = load_excalidraw_library(lib_path)
                prefer_library = True
            except Exception as e:
                print(f"warning: failed to load library {lib_path}: {e}", file=sys.stderr)

    scene, meta2 = build_scene(
        text=text,
        preset=preset,
        size=size,
        theme=theme,
        fidelity=fidelity,
        seed=seed,
        strict=strict,
        library=library,
        prefer_library=prefer_library,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scene, f, indent=2, ensure_ascii=False)
        f.write("\n")

    latest_dir = os.path.join(out_root, "excalidraw")
    os.makedirs(latest_dir, exist_ok=True)
    latest_path = os.path.join(latest_dir, f"latest-{slug}.excalidraw")
    try:
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(scene, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass

    meta = {
        "name": slug,
        "preset": preset.name,
        "theme": theme.name,
        "fidelity": fidelity,
        "grid": preset.grid_size,
        "seed": seed,
        "library": bool(library),
        "library_used": meta2.get("library_used"),
        "layout": meta2["layout"],
        "screens": meta2["screens"],
        "output_path": os.path.abspath(out_path),
        "latest_path": os.path.abspath(latest_path),
    }

    if args.json:
        print(json.dumps(meta))
    else:
        print(out_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
