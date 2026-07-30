#!/usr/bin/env python3
"""CI guard: the client version is consistent and never runs ahead of releases.

- The py_wheel version in client/BUILD.bazel (the single source of package
  metadata — this is what CI publishes) must match sigbot_client.__version__.
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


def fail(msg: str) -> None:
    sys.exit(f"version check FAILED: {msg}")


def parse(version: str, source: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        fail(f"unparseable version {version!r} from {source} (want X.Y.Z)")
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent

    build = (root / "client" / "BUILD.bazel").read_text()
    m = re.search(r'version = "(\d+\.\d+\.\d+)"', build)
    if not m:
        fail("no py_wheel version found in client/BUILD.bazel")
    wheel_version = m.group(1)

    init = (root / "client" / "sigbot_client" / "__init__.py").read_text()
    m = re.search(r'__version__ = "([^"]+)"', init)
    if not m:
        fail("no __version__ found in client/sigbot_client/__init__.py")
    module_version = m.group(1)

    if wheel_version != module_version:
        fail(f"py_wheel in client/BUILD.bazel says {wheel_version} but "
             f"sigbot_client.__version__ says {module_version} — bump both together")
    version = parse(wheel_version, "client/BUILD.bazel")

    tags = subprocess.run(["git", "tag", "-l", "v*"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.split()
    releases = [parse(t[1:], f"tag {t}") for t in tags
                if re.fullmatch(r"v\d+\.\d+\.\d+", t)]

    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            fail(f"release tag {tag!r} is not vX.Y.Z")
        if parse(tag[1:], f"tag {tag}") != version:
            fail(f"release tag {tag} does not match client version {wheel_version} "
                 f"— retag after bumping client/BUILD.bazel + sigbot_client.__version__")
        print(f"ok: releasing sigbot-client {wheel_version} from tag {tag}")
        return

    if not releases:
        print(f"ok: client version {wheel_version}, no v* release tags yet")
        return
    latest = max(releases)
    if version > latest:
        fail(f"client version {wheel_version} is ahead of the latest release tag "
             f"v{'.'.join(map(str, latest))} — tag v{wheel_version} to release it "
             f"(or revert the bump)")
    print(f"ok: client version {wheel_version} <= latest release tag "
          f"v{'.'.join(map(str, latest))}")


if __name__ == "__main__":
    main()
