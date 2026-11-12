#!/usr/bin/env python3
"""Apply the custom UI patches to a Zed checkout.

This script is deliberately small and strict: every replacement must match
exactly once. If upstream refactors the target code, the build fails loudly
instead of producing a silently broken binary.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

STYLE_OLD = """        let rem_size = window.rem_size();
        let corner_radii = self
            .corner_radii
            .to_pixels(rem_size)
            .clamp_radii_for_quad_size(bounds.size);
"""

STYLE_NEW = """        let rem_size = window.rem_size();
        let raw_radii = self.corner_radii.to_pixels(rem_size);
        let corner_radii = if raw_radii.top_left >= px(100.) {
            raw_radii.clamp_radii_for_quad_size(bounds.size)
        } else {
            Corners::all(px(0.))
        };
"""

SHADOW_PATCHES = {
    "crates/ui/src/components/context_menu.rs": 2,
    "crates/ui/src/components/popover.rs": 2,
}


def replace_exact(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"error: expected exactly one match in {path}, found {count}\n"
            "       upstream code changed; update overlay/apply.py"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {path}")


def add_shadow_none(path: Path, expected: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    result: list[str] = []
    found = 0
    inserted = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped != ".elevation_2(cx)":
            result.append(line)
            continue

        found += 1
        indent = line[: len(line) - len(line.lstrip())]
        result.append(line)
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if following.strip() != ".shadow_none()":
            ending = line[len(line.rstrip("\r\n")) :]
            result.append(f"{indent}.shadow_none(){ending}")
            inserted += 1

    if found != expected:
        raise SystemExit(
            f"error: expected {expected} '.elevation_2(cx)' lines in {path}, "
            f"found {found}"
        )
    if inserted == 0:
        print(f"already patched {path}")
        return

    path.write_text("".join(result), encoding="utf-8")
    print(f"patched {path} ({inserted} menus)")


def print_diff(zed_root: Path, files: list[Path]) -> None:
    root = zed_root.resolve()
    relative = []
    for file in files:
        try:
            relative.append(str(file.resolve().relative_to(root)))
        except ValueError:
            continue
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--stat", "--", *relative],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return
    if result.stdout.strip():
        print(result.stdout.rstrip())


def install_theme(zed_root: Path) -> None:
    source = HERE / "vscode-dark-modern.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    for theme in data.get("themes", []):
        style = theme.setdefault("style", {})
        if "border.variant" in style:
            style["border.variant"] = "#2B2B2B"

    destination = zed_root / "assets" / "themes" / "vscode-dark-modern.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    installed = json.loads(destination.read_text(encoding="utf-8"))
    borders = [
        theme.get("style", {}).get("border.variant")
        for theme in installed.get("themes", [])
    ]
    if not borders or any(value != "#2B2B2B" for value in borders):
        raise SystemExit("error: border.variant was not applied to every bundled theme")
    print(f"installed {destination}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed-root", required=True, type=Path)
    args = parser.parse_args()

    zed_root = args.zed_root.resolve()
    if not (zed_root / "crates" / "gpui" / "src" / "style.rs").is_file():
        raise SystemExit(f"error: not a Zed checkout: {zed_root}")

    install_theme(zed_root)

    style_path = zed_root / "crates" / "gpui" / "src" / "style.rs"
    replace_exact(style_path, STYLE_OLD, STYLE_NEW)

    for relative, expected in SHADOW_PATCHES.items():
        add_shadow_none(zed_root / relative, expected)

    touched = [
        zed_root / "assets" / "themes" / "vscode-dark-modern.json",
        style_path,
        *(zed_root / relative for relative in SHADOW_PATCHES),
    ]
    print_diff(zed_root, touched)
    print("custom UI patches applied successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
