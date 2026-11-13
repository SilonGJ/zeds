#!/usr/bin/env python3
"""Resolve which localized Zed release to build.

The workflow is driven by zed-i18n releases instead of Zed releases: zed-i18n
pins an exact Zed tag and ships the matching translation manifest, so building
anything else would produce stale translation occurrences.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

I18N_REPOSITORY = "LI-NA/zed-i18n"
USER_AGENT = "zeds-custom-build"


def request(url: str, token: str | None) -> urllib.response.addinfourl:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=60
    )


def api_json(url: str, token: str | None) -> dict:
    with request(url, token) as response:
        return json.loads(response.read().decode("utf-8"))


def release_exists(repository: str, tag: str, token: str | None) -> bool:
    encoded_tag = urllib.parse.quote(tag, safe="")
    url = f"https://api.github.com/repos/{repository}/releases/tags/{encoded_tag}"
    try:
        api_json(url, token)
        return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def release_matches(repository: str, base_tag: str, token: str | None) -> bool:
    """Whether `base_tag` or any rebuild tag of it (`-r2`, `-r3`, ...) exists.

    A manual rebuild supersedes the default revision, so a later scheduled run
    must not republish the older default tag as the newest release.
    """
    page = 1
    while True:
        releases = api_json(
            f"https://api.github.com/repos/{repository}/releases"
            f"?per_page=100&page={page}",
            token,
        )
        if not releases:
            return False
        for release in releases:
            tag = release.get("tag_name", "")
            if tag == base_tag or tag.startswith(f"{base_tag}-r"):
                return True
        if len(releases) < 100:
            return False
        page += 1


def read_project_config(tag: str) -> dict:
    encoded_tag = urllib.parse.quote(tag, safe="")
    url = (
        f"https://raw.githubusercontent.com/{I18N_REPOSITORY}/"
        f"{encoded_tag}/config/project.toml"
    )
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60
    ) as response:
        return tomllib.loads(response.read().decode("utf-8"))


def write_outputs(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            for key, value in values.items():
                output.write(f"{key}={value}\n")


def write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    if not repository:
        print("error: GITHUB_REPOSITORY is not set", file=sys.stderr)
        return 1

    requested_tag = os.environ.get("INPUT_I18N_TAG", "").strip()
    requested_revision = os.environ.get("INPUT_REVISION", "").strip()

    if requested_tag:
        i18n_tag = requested_tag
    else:
        release = api_json(
            f"https://api.github.com/repos/{I18N_REPOSITORY}/releases/latest", token
        )
        i18n_tag = release["tag_name"]

    project = read_project_config(i18n_tag)
    zed_version = project.get("zed_version", "").strip()
    zed_commit = project.get("zed_commit", "").strip()
    if not zed_version:
        print(f"error: {i18n_tag} has no zed_version", file=sys.stderr)
        return 1

    match = re.search(r"-i18n\.(\d+)$", i18n_tag)
    default_revision = int(match.group(1)) if match else 1

    if requested_revision:
        try:
            revision = int(requested_revision)
        except ValueError:
            print(f"error: invalid revision: {requested_revision!r}", file=sys.stderr)
            return 1
    else:
        revision = default_revision

    base_tag = f"zeds-{i18n_tag}"
    release_tag = base_tag
    if revision != default_revision:
        release_tag += f"-r{revision}"

    if revision == default_revision:
        # Scheduled runs use the default revision; skip them when a manual
        # rebuild of the same i18n tag already exists.
        exists = release_matches(repository, base_tag, token)
    else:
        exists = release_exists(repository, release_tag, token)
    should_build = not exists

    values = {
        "should_build": "true" if should_build else "false",
        "i18n_tag": i18n_tag,
        "zed_version": zed_version,
        "zed_commit": zed_commit,
        "revision": str(revision),
        "release_tag": release_tag,
    }
    write_outputs(values)

    status = "build" if should_build else "skip (release already exists)"
    print(
        f"{status}: zed={zed_version} i18n={i18n_tag} "
        f"revision={revision} release={release_tag}"
    )
    write_summary(
        [
            "## Custom Zed build",
            "",
            f"- Zed version: `{zed_version}`",
            f"- zed-i18n tag: `{i18n_tag}`",
            f"- Update revision: `{revision}`",
            f"- Release tag: `{release_tag}`",
            f"- Result: **{status}**",
        ]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
