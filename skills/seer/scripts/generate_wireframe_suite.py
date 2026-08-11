#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SuiteCase:
    name: str
    prompt: str


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _excalidraw_generator() -> Path:
    return _script_dir() / "excalidraw_from_text.py"


def _default_out_dir() -> Path:
    out_root = os.environ.get("SEER_OUT_DIR") or os.environ.get("SEER_TMP_DIR") or ".seer"
    return Path(out_root) / "excalidraw"


def _suite_cases() -> list[SuiteCase]:
    # Keep prompts short + structured so mapping behavior is easy to verify visually.
    return [
        SuiteCase(
            name="auth-sign-in",
            prompt="""
screen: Sign in
  header: Sign in
  text: Welcome back. Please sign in to continue.
  input: Email
  input: Password
  checkbox: Remember me (checked)
  button: Continue
  divider:
  button: Sign in with Google
  text: Forgot password?
  footer: Home | Search | Cart | Profile
""".strip(),
        ),
        SuiteCase(
            name="settings-preferences",
            prompt="""
screen: Preferences
  header: Settings
  section: Account
  list: Profile, Security, Notifications
  section: Preferences
  toggle: Dark mode (on)
  toggle: Email alerts (off)
  radio: Compact layout (selected)
  radio: Comfortable layout
  button: Save
""".strip(),
        ),
        SuiteCase(
            name="support-tabs",
            prompt="""
screen: Support
  header: Support
  tabs: Help | Contact | About
  card: Getting started
  text: Find answers or reach out to us.
  button: Contact support
""".strip(),
        ),
        SuiteCase(
            name="search-and-filters",
            prompt="""
screen: Search
  header: Search
  input: Search
  chips: All, New, Trending, Recommended
  dropdown: Sort by
  list: Result A, Result B, Result C, Result D
  button: Apply
""".strip(),
        ),
        SuiteCase(
            name="compose-form",
            prompt="""
screen: Create post
  header: New post
  input: Title
  textarea: Description
  dropdown: Category
  chips: #design, #mobile, #wireframe
  button: Publish
""".strip(),
        ),
        SuiteCase(
            name="product-grid",
            prompt="""
screen: Products
  header: Shop
  card: Promo banner
  image: Hero image
  list: Item 1, Item 2, Item 3, Item 4
  button: View cart
  footer: Home | Category | Cart | Wishlist | Profile
""".strip(),
        ),
        SuiteCase(
            name="multi-screen-flow",
            prompt="""
screen: Home
  header: Home
  card: Welcome banner
  input: Search
  list: Trending, New releases, Recommended
  button: Explore

screen: Settings
  header: Settings
  section: Account
  list: Profile, Security, Notifications
  section: Support
  tabs: Help | Contact | About
  button: Log out
""".strip(),
        ),
        SuiteCase(
            name="explicit-library-items",
            prompt="""
screen: Library sanity
  header: Library sanity
  lib: navigation bar | Library sanity
  lib: tabs | One | Two | Three
  lib: dropdown | Country
  lib: textarea | Notes
  lib: checkbox-off | Subscribe
  lib: radiobutton-off | Option
  lib: toggle-off | Alerts
""".strip(),
        ),
    ]


def _run_case(
    *,
    generator: Path,
    case: SuiteCase,
    theme: str,
    fidelity: str,
    preset: str | None,
    library: Path | None,
    no_library: bool,
    out_dir: Path,
) -> dict[str, Any]:
    out_path = out_dir / f"{case.name}.excalidraw"
    cmd = [
        sys.executable,
        str(generator),
        "--name",
        case.name,
        "--out",
        str(out_path),
        "--json",
        "--theme",
        theme,
        "--fidelity",
        fidelity,
    ]
    if preset:
        cmd.extend(["--preset", preset])
    if library:
        cmd.extend(["--library", str(library)])
    if no_library:
        cmd.append("--no-library")
    p = subprocess.run(
        cmd,
        input=case.prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"case {case.name} failed (exit {p.returncode})\n"
            f"stderr:\n{p.stderr.decode('utf-8', errors='replace')}\n"
        )
    try:
        meta = json.loads(p.stdout.decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"case {case.name} returned non-JSON output: {e}\n{p.stdout!r}") from e

    actual_path = Path(str(meta.get("output_path") or ""))
    if not actual_path.is_file() or actual_path.parent.resolve() != out_dir.resolve():
        raise RuntimeError(f"case {case.name} wrote outside --out-dir: {actual_path}")
    scene = json.loads(actual_path.read_text(encoding="utf-8"))
    if scene.get("type") != "excalidraw" or scene.get("version") != 2 or not isinstance(scene.get("elements"), list):
        raise RuntimeError(f"case {case.name} produced an invalid Excalidraw scene")
    if scene.get("scrollToContent") is not True:
        raise RuntimeError(f"case {case.name} did not request initial content centering")
    if {"scrollX", "scrollY", "zoom"} & set(scene.get("appState") or {}):
        raise RuntimeError(f"case {case.name} emitted viewport-dependent appState")
    library_used = meta.get("library_used") or {}
    if library_used.get("loaded") and int(library_used.get("elements_total") or 0) <= 0:
        raise RuntimeError(f"case {case.name} loaded a library but used no library elements")
    return meta


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate a suite of .excalidraw wireframes for component mapping QA.")
    parser.add_argument("--out-dir", help="Output directory (default: $SEER_OUT_DIR/excalidraw)", default=None)
    parser.add_argument("--theme", default="classic", help="Theme passed to excalidraw_from_text.py")
    parser.add_argument("--fidelity", default="medium", choices=["low", "medium", "high"], help="Fidelity passed to excalidraw_from_text.py")
    parser.add_argument("--preset", default=None, choices=["mobile", "tablet", "desktop"], help="Preset passed to excalidraw_from_text.py")
    parser.add_argument("--library", default=None, help="Library passed to excalidraw_from_text.py")
    parser.add_argument("--no-library", action="store_true", help="Disable library usage")
    parser.add_argument("--filter", default=None, help="Only run cases whose name matches this regex")
    parser.add_argument("--manifest", default=None, help="Write a JSON manifest of outputs to this file")
    args = parser.parse_args(argv)

    if args.library and args.no_library:
        print("error: pass only one of --library or --no-library", file=sys.stderr)
        return 2
    library_path = Path(args.library).expanduser() if args.library else None
    if library_path and not library_path.is_file():
        print(f"error: library not found: {library_path}", file=sys.stderr)
        return 2

    generator = _excalidraw_generator()
    if not generator.exists():
        print(f"error: missing generator: {generator}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir).expanduser() if args.out_dir else _default_out_dir()
    cases = _suite_cases()
    if args.filter:
        try:
            rx = re.compile(args.filter)
        except re.error as e:
            print(f"error: invalid --filter regex: {e}", file=sys.stderr)
            return 2
        cases = [c for c in cases if rx.search(c.name)]
        if not cases:
            print(f"error: no suite cases matched filter: {args.filter}", file=sys.stderr)
            return 2

    manifest_path = Path(args.manifest).expanduser() if args.manifest else (out_dir / "suite-manifest.json")
    out_dir_resolved = out_dir.resolve()
    manifest_resolved = manifest_path.resolve()
    generated_paths = {
        path.resolve()
        for case in cases
        for path in (out_dir / f"{case.name}.excalidraw", out_dir / f"latest-{case.name}.excalidraw")
    }
    if manifest_path.is_dir():
        print(f"error: manifest path is a directory: {manifest_path}", file=sys.stderr)
        return 2
    if (
        manifest_resolved == out_dir_resolved
        or manifest_resolved in out_dir_resolved.parents
        or any(path == manifest_resolved or path in manifest_resolved.parents for path in generated_paths)
    ):
        print(f"error: manifest path collides with suite outputs: {manifest_path}", file=sys.stderr)
        return 2
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"error: cannot create output directory: {e}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            meta = _run_case(
                generator=generator,
                case=case,
                theme=args.theme,
                fidelity=args.fidelity,
                preset=args.preset,
                library=library_path,
                no_library=args.no_library,
                out_dir=out_dir,
            )
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        results.append(
            {
                "name": case.name,
                "output_path": meta.get("output_path"),
                "latest_path": meta.get("latest_path"),
                "library_used": meta.get("library_used"),
                "screens": meta.get("screens"),
            }
        )

    try:
        manifest_path.write_text(json.dumps({"generated": results}, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"error: failed to write manifest {manifest_path}: {e}", file=sys.stderr)
        return 2

    print(json.dumps({"count": len(results), "manifest": str(manifest_path), "cases": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
