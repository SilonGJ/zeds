#!/usr/bin/env python3
"""Collect the Windows artifacts and generate manifest.json for auto-updates."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from tools.zed_i18n.ci_release import create_windows_portable_zip

ARCH = "x86_64"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset(path: Path, kind: str, locale: str, repository: str, tag: str) -> dict:
    return {
        "kind": kind,
        "locale": locale,
        "platform": "windows",
        "arch": ARCH,
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": sha256(path),
        "download_url": (
            f"https://github.com/{repository}/releases/download/"
            f"{urllib.parse.quote(tag, safe='')}/{urllib.parse.quote(path.name)}"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed-root", required=True, type=Path)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--zed-version", required=True)
    parser.add_argument("--i18n-tag", required=True)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--locale", default="zh-CN")
    parser.add_argument("--arch", default=ARCH)
    args = parser.parse_args()

    zed_root = args.zed_root.resolve()
    dist = args.dist_dir.resolve()
    dist.mkdir(parents=True, exist_ok=True)

    installer_source = zed_root / "target" / f"Zed-i18n-{args.arch}.exe"
    if not installer_source.is_file():
        raise SystemExit(f"error: installer not found: {installer_source}")

    installer = dist / f"Zed-i18n-{args.locale}-windows-{args.arch}.exe"
    shutil.copy2(installer_source, installer)

    portable = dist / f"Zed-i18n-{args.locale}-windows-{args.arch}.zip"
    create_windows_portable_zip(zed_root, args.arch, portable)

    zed_version = args.zed_version.removeprefix("v")
    manifest = {
        "schema": 2,
        "zed_version": zed_version,
        "i18n_revision": args.revision,
        "release_tag": args.release_tag,
        "repository": args.repository,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "assets": [
            asset(installer, "app", args.locale, args.repository, args.release_tag),
            asset(portable, "portable_app", args.locale, args.repository, args.release_tag),
        ],
    }

    manifest_path = dist / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    checksum_lines = [
        f"{sha256(path)}  {path.name}"
        for path in (installer, portable, manifest_path)
    ]
    (dist / "SHA256SUMS.txt").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )

    notes = f"""Zed {zed_version} · 简体中文 · Windows x86_64

- 本地化来源：`{args.i18n_tag}`
- 界面修改：全局直角、移除菜单弹出层阴影
- 内置主题：VSCode Dark Modern（`border.variant` 为 `#2B2B2B`），未设为默认
- 自动更新：已指向本仓库的 `manifest.json`
- 安装包未签名，SmartScreen 可能提示；请核对 SHA256SUMS.txt

首次需要手动运行安装包；之后的新版本会由 Zed 内置更新器提示。
"""
    (dist / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")

    print(f"wrote {installer}")
    print(f"wrote {portable}")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
