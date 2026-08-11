#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import excalidraw_from_text as generator  # noqa: E402


class ExcalidrawGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assets = SCRIPT_DIR.parent / "assets" / "excalidraw"
        cls.library = generator.load_excalidraw_library(assets / "wireframe-ui-kit.excalidrawlib")
        cls.fallback_library = generator.load_excalidraw_library(assets / "basic-ux-wireframing-elements.excalidrawlib")

    def build(
        self,
        text: str,
        *,
        library: generator.ExcalidrawLibrary | None = None,
        size: tuple[int, int] | None = None,
    ) -> dict:
        scene, _ = generator.build_scene(
            text=text,
            preset=generator.PRESETS["mobile"],
            size=size,
            theme=generator.THEMES["classic"],
            fidelity="medium",
            seed=7,
            strict=True,
            library=library,
            prefer_library=library is not None,
        )
        return scene

    @staticmethod
    def visible_texts(scene: dict) -> list[str]:
        return [
            element["text"]
            for element in scene["elements"]
            if element.get("type") == "text" and element.get("opacity", 100) > 0
        ]

    def assert_inside_screen(self, scene: dict) -> None:
        screen = next(
            element
            for element in scene["elements"]
            if (element.get("customData") or {}).get("seerLabel") == "screen"
        )
        sx0, sy0 = screen["x"], screen["y"]
        sx1, sy1 = sx0 + screen["width"], sy0 + screen["height"]
        for element in scene["elements"]:
            if element is screen:
                continue
            x0, y0, x1, y1 = generator._bbox_for_element(element)
            self.assertGreaterEqual(x0, sx0 - 1, element.get("text") or element["id"])
            self.assertGreaterEqual(y0, sy0 - 1, element.get("text") or element["id"])
            self.assertLessEqual(x1, sx1 + 1, element.get("text") or element["id"])
            self.assertLessEqual(y1, sy1 + 1, element.get("text") or element["id"])

    def test_component_labels_parsing_and_scene_state(self) -> None:
        scene = self.build(
            "screen: Controls; input: Search; checkbox: Subscribe (checked); "
            "radio: Compact (selected); toggle: Alerts (on); tabs: One | Two | Three",
            library=self.library,
        )
        texts = self.visible_texts(scene)
        for expected in ("Search", "Subscribe", "Compact", "Alerts", "One", "Two", "Three"):
            self.assertIn(expected, texts)
        self.assertTrue(scene["scrollToContent"])
        self.assertFalse({"scrollX", "scrollY", "zoom"} & set(scene["appState"]))
        screen = next(e for e in scene["elements"] if (e.get("customData") or {}).get("seerLabel") == "screen")
        self.assertEqual((screen["width"], screen["height"]), (390.0, 844.0))
        self.assertEqual(generator._pick_library_item_for_component(self.library, "input", "Search").name, "search")
        self.assertEqual(list(generator._iter_phrases("header: Home | button: Go")), ["header: Home", "button: Go"])
        self.assertEqual(list(generator._iter_phrases("tabs: One | Two | Three")), ["tabs: One | Two | Three"])

    def test_explicit_tabs_and_all_library_items_are_valid(self) -> None:
        scene = self.build("lib: tabs | One | Two | Three", library=self.library)
        texts = self.visible_texts(scene)
        self.assertTrue({"One", "Two", "Three"}.issubset(texts))
        self.assertFalse({"Tab1", "Tab2", "Tab3"} & set(texts))
        self.assertEqual(self.visible_texts(self.build("tabs: One", library=self.library)), ["One"])
        self.assertEqual(self.visible_texts(self.build("lib: tabs | One | Two", library=self.library)), ["One", "Two"])
        for item, label in (("dropdown", "Country"), ("textarea", "Notes"), ("product image", "Hero")):
            labeled_scene = self.build(f"lib: {item} | {label}", library=self.library)
            self.assertEqual(self.visible_texts(labeled_scene), [label])
            self.assert_inside_screen(labeled_scene)

        builder = generator.ExcalidrawBuilder(
            rng=random.Random(1),
            grid=20,
            theme=generator.THEMES["classic"],
            fidelity="medium",
        )
        for library in (self.library, self.fallback_library):
            for item in library.items:
                group = generator.instantiate_library_item(
                    builder=builder,
                    item=item,
                    x=0,
                    y=0,
                    label_override=None,
                    seer_label="test",
                )
                generator._validate_scene({"elements": group}, grid=20)

    def test_layout_wraps_and_omits_without_overflow(self) -> None:
        prompts = [
            "text: This long sentence must wrap inside a narrow mobile wireframe instead of escaping it.",
            "text: 아주 긴 한국어 문장도 모바일 와이어프레임 바깥으로 벗어나지 않고 안전하게 줄바꿈되어야 합니다.",
            "\n".join(["screen: Overflow"] + [f"button: Action {index}" for index in range(30)]),
            "list: " + ", ".join(f"Item {index}" for index in range(30)),
            "chips: " + ", ".join(f"Long filter {index}" for index in range(12)),
        ]
        for prompt in prompts:
            scene = self.build(prompt, library=self.library)
            self.assert_inside_screen(scene)
        overflow_scene = self.build(prompts[2], library=self.library)
        omitted = [
            e for e in overflow_scene["elements"] if (e.get("customData") or {}).get("seerLabel") == "omitted"
        ]
        self.assertEqual(len(omitted), 1)
        self.assert_inside_screen(self.build(prompts[2], library=self.library, size=(100, 100)))

        long_label_scene = self.build(
            "section: " + "This is a very long section heading " * 8 + "\ncheckbox: " + "Long label " * 20,
            library=self.library,
        )
        self.assert_inside_screen(long_label_scene)
        visible_font_sizes = [
            float(e["fontSize"])
            for e in long_label_scene["elements"]
            if e.get("type") == "text" and e.get("opacity", 100) > 0
        ]
        self.assertGreaterEqual(min(visible_font_sizes), 10)

        chip_scene = self.build("chips: " + "Long filter label " * 20, library=self.library)
        self.assert_inside_screen(chip_scene)
        chip_fonts = [
            float(e["fontSize"])
            for e in chip_scene["elements"]
            if (e.get("customData") or {}).get("seerLabel") == "chips" and e.get("type") == "text"
        ]
        self.assertGreaterEqual(min(chip_fonts), 10)

    def test_fallback_library_uses_labeled_controls(self) -> None:
        expected = {
            ("button", "Continue"): "Filled button (text only)",
            ("input", "Search"): "Search field",
            ("textarea", "Notes"): "Text area with placeholder",
            ("checkbox", "Subscribe"): "Checkbox (text+icon)",
            ("radio", "Option"): "Radio button (text+icon)",
            ("toggle", "Alerts"): "Boxed toggle with text (OFF)",
        }
        for (component, label), item_name in expected.items():
            item = generator._pick_library_item_for_component(self.fallback_library, component, label)
            self.assertIsNotNone(item)
            self.assertEqual(item.name, item_name)

    def test_cli_paths_suite_and_determinism(self) -> None:
        first = self.build("header: Home; button: Continue", library=self.library)
        second = self.build("header: Home; button: Continue", library=self.library)
        self.assertEqual(first, second)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "excalidraw_from_text.py"),
                    "--text",
                    "tabs: One | Two | Three",
                    "--out",
                    "scene.excalidraw",
                    "--json",
                ],
                cwd=root,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "scene.excalidraw").is_file())
            self.assertTrue((root / "latest-wireframe.excalidraw").is_file())

            missing_library = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "excalidraw_from_text.py"),
                    "--text",
                    "button: Go",
                    "--library",
                    str(root / "missing.excalidrawlib"),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(missing_library.returncode, 2)

            blocking_path = root / "not-a-directory"
            blocking_path.write_text("file", encoding="utf-8")
            bad_commands = [
                [str(SCRIPT_DIR / "excalidraw_from_text.py"), "--text", "button: Go", "--out", str(root)],
                [
                    str(SCRIPT_DIR / "excalidraw_from_text.py"),
                    "--text",
                    "button: Go",
                    "--out",
                    str(blocking_path / "scene.excalidraw"),
                ],
                [str(SCRIPT_DIR / "generate_wireframe_suite.py"), "--out-dir", str(blocking_path)],
                [
                    str(SCRIPT_DIR / "generate_wireframe_suite.py"),
                    "--out-dir",
                    str(root / "bad-manifest-suite"),
                    "--filter",
                    "support-tabs",
                    "--manifest",
                    str(root),
                ],
                [
                    str(SCRIPT_DIR / "generate_wireframe_suite.py"),
                    "--out-dir",
                    str(root / "collision-suite"),
                    "--filter",
                    "support-tabs",
                    "--manifest",
                    str(root / "collision-suite" / "support-tabs.excalidraw"),
                ],
                [
                    str(SCRIPT_DIR / "generate_wireframe_suite.py"),
                    "--out-dir",
                    str(root / "future-suite"),
                    "--filter",
                    "support-tabs",
                    "--manifest",
                    str(root / "future-suite"),
                ],
                [
                    str(SCRIPT_DIR / "generate_wireframe_suite.py"),
                    "--out-dir",
                    str(root / "nested-collision-suite"),
                    "--filter",
                    "support-tabs",
                    "--manifest",
                    str(root / "nested-collision-suite" / "support-tabs.excalidraw" / "manifest.json"),
                ],
            ]
            for command in bad_commands:
                failed = subprocess.run([sys.executable, *command], text=True, capture_output=True)
                self.assertEqual(failed.returncode, 2, failed.stderr)
                self.assertNotIn("Traceback", failed.stderr)
            self.assertFalse((root / "collision-suite" / "support-tabs.excalidraw").exists())
            self.assertFalse((root / "future-suite").exists())
            self.assertFalse((root / "nested-collision-suite").exists())

            suite_dir = root / "suite"
            manifest_path = root / "nested" / "suite.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "generate_wireframe_suite.py"),
                    "--out-dir",
                    str(suite_dir),
                    "--filter",
                    "support-tabs",
                    "--manifest",
                    str(manifest_path),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            meta = json.loads(result.stdout)
            self.assertEqual(Path(meta["cases"][0]["output_path"]).parent, suite_dir)
            self.assertTrue(manifest_path.is_file())


if __name__ == "__main__":
    unittest.main()
