#!/usr/bin/env python3
"""Write a version into the two files that carry it.

`custom_components/skylight/manifest.json` is the one Home Assistant and HACS
read; `pyproject.toml` mirrors it for the dev toolchain. The release workflow
calls this so a release needs no file edited by hand.

    $ python3 scripts/set_version.py 2026.8.3
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

CALVER = re.compile(r"^(\d{4})\.(1[0-2]|[1-9])\.(0|[1-9]\d*)$")
PROJECT_VERSION = re.compile(r'(^\[project\]$.*?^version = ")[^"]+(")', re.DOTALL | re.MULTILINE)


def main() -> int:
    """Write the version, or explain why it will not."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="calendar version, e.g. 2026.8.3")
    args = parser.parse_args()

    if not CALVER.match(args.version):
        print(f"{args.version!r} is not a calendar version (YYYY.M.N)", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text())
    manifest["version"] = args.version
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")

    pyproject = PYPROJECT.read_text()
    updated, count = PROJECT_VERSION.subn(rf"\g<1>{args.version}\g<2>", pyproject, count=1)
    if count != 1:
        print(f"no [project] version found in {PYPROJECT}", file=sys.stderr)
        return 1
    PYPROJECT.write_text(updated)

    print(args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
