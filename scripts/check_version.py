#!/usr/bin/env python3
"""Fail if the integration version is malformed or out of sync.

The version lives in two places — `custom_components/skylight/manifest.json`,
which is what Home Assistant and HACS read, and `pyproject.toml`, which is only
the dev toolchain. They must agree, and both must be calendar versions of the
form ``YYYY.M.N``.

Pass ``--tag v2026.8.0`` to also check that a release tag names that version;
the release workflow does this so a mistyped tag cannot ship.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "custom_components" / "skylight" / "manifest.json"
PYPROJECT = ROOT / "pyproject.toml"

# YYYY.M.N: four-digit year, unpadded month, point release starting from 0.
CALVER = re.compile(r"^(\d{4})\.(1[0-2]|[1-9])\.(0|[1-9]\d*)$")
PROJECT_VERSION = re.compile(r'^\[project\]$.*?^version = "([^"]+)"', re.DOTALL | re.MULTILINE)


def project_version() -> str:
    """Read `version` from pyproject.toml's `[project]` table.

    Read by hand rather than with `tomllib`: this runs under whatever `python3`
    is on the path, including the 3.9 that ships with macOS, and pulling in the
    project's own interpreter would make a cheap hook slow.
    """
    match = PROJECT_VERSION.search(PYPROJECT.read_text())
    if match is None:
        raise SystemExit(f"no [project] version found in {PYPROJECT}")
    return match.group(1)


def main() -> int:
    """Check the version, and optionally the tag naming it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="release tag to check against, e.g. v2026.8.0")
    args = parser.parse_args()

    manifest_version = json.loads(MANIFEST.read_text())["version"]
    pyproject_version = project_version()

    errors: list[str] = []
    if not CALVER.match(manifest_version):
        errors.append(
            f"manifest.json version {manifest_version!r} is not a calendar version "
            "(YYYY.M.N, month unpadded, point release from 0)"
        )
    if manifest_version != pyproject_version:
        errors.append(
            f"manifest.json says {manifest_version!r} but pyproject.toml says "
            f"{pyproject_version!r}; they must match"
        )
    if args.tag is not None and args.tag != f"v{manifest_version}":
        errors.append(f"tag {args.tag!r} does not name version {manifest_version!r}")

    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
