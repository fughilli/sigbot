#!/usr/bin/env python3
"""CI guard: the client version is consistent and never runs ahead of releases.

- client/pyproject.toml and the py_wheel version in client/BUILD.bazel agree.
- On a tag build (GITHUB_REF_TYPE=tag) the version must EQUAL the tag, so the
  wheel published to PyPI/the release page is the version the tag claims.
- On any other build the version must be <= the latest v* tag: a version
  bumped past every release means the release/tag step was forgotten.

Needs tags in the checkout (actions/checkout with fetch-tags). Stdlib-only.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tomllib


def fail(msg: str) -> None:
    sys.exit(f"version check FAILED: {msg}")


def parse(version: str, source: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        fail(f"unparseable version {version!r} from {source} (want X.Y.Z)")
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def build_attr(build: str, attr: str) -> str:
    m = re.search(rf'{attr} = "([^"]*)"', build)
    if not m:
        fail(f"no py_wheel {attr} found in client/BUILD.bazel")
    return m.group(1)  # type: ignore[union-attr]


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent

    project = tomllib.loads((root / "client" / "pyproject.toml").read_text())["project"]
    py_version = project["version"]
    build = (root / "client" / "BUILD.bazel").read_text()

    # The bazel py_wheel is what CI publishes; pyproject drives pip builds.
    # Every field they both define must agree.
    for label, expected, attr in [
        ("version", py_version, "version"),
        ("description/summary", project["description"], "summary"),
        ("requires-python", project["requires-python"], "python_requires"),
        ("license", project["license"]["text"], "license"),
        ("Homepage url", project["urls"]["Homepage"], None),
    ]:
        actual = (build_attr(build, attr) if attr
                  else (re.search(r'"Homepage": "([^"]*)"', build) or fail(
                      "no Homepage in py_wheel project_urls")).group(1))
        if actual != expected:
            fail(f"{label} mismatch: pyproject.toml says {expected!r}, "
                 f"BUILD.bazel py_wheel says {actual!r} — keep them in sync")
    version = parse(py_version, "client/pyproject.toml")

    tags = subprocess.run(["git", "tag", "-l", "v*"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.split()
    releases = [parse(t[1:], f"tag {t}") for t in tags
                if re.fullmatch(r"v\d+\.\d+\.\d+", t)]

    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            fail(f"release tag {tag!r} is not vX.Y.Z")
        if parse(tag[1:], f"tag {tag}") != version:
            fail(f"release tag {tag} does not match client version {py_version} "
                 f"— retag after bumping client/pyproject.toml + client/BUILD.bazel")
        print(f"ok: releasing sigbot-client {py_version} from tag {tag}")
        return

    if not releases:
        print(f"ok: client version {py_version}, no v* release tags yet")
        return
    latest = max(releases)
    if version > latest:
        fail(f"client version {py_version} is ahead of the latest release tag "
             f"v{'.'.join(map(str, latest))} — tag v{py_version} to release it "
             f"(or revert the bump)")
    print(f"ok: client version {py_version} <= latest release tag "
          f"v{'.'.join(map(str, latest))}")


if __name__ == "__main__":
    main()
