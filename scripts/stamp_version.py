#!/usr/bin/env python3
"""Write a released version into `manifest.json`.

The version is not committed. `hacs.json` sets `zip_release`, so HACS installs
the zip the release workflow attaches rather than the source tree, and the
repository carries a `0000.0.0` placeholder that this replaces at release time.
Keeping releasing off `main` that way is what removes the release commit, and
with it the pull request and everything branch protection then demanded of it.

    $ python3 scripts/stamp_version.py 2026.9.0

Refuses anything that is not `YYYY.M.N`, and refuses to run twice — a manifest
that already carries a real version means the checkout is not what this expects.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

MANIFEST = pathlib.Path("custom_components/skylight/manifest.json")
PLACEHOLDER = "0000.0.0"
VERSION = re.compile(r"^\d{4}\.(1[0-2]|[1-9])\.(0|[1-9]\d*)$")


def stamp(manifest: pathlib.Path, version: str) -> None:
    """Replace the placeholder version in `manifest` with `version`."""
    if not VERSION.match(version):
        raise SystemExit(f"{version} is not a YYYY.M.N version")

    data = json.loads(manifest.read_text())
    current = data.get("version")
    if current != PLACEHOLDER:
        raise SystemExit(f"{manifest} holds {current!r}, expected the {PLACEHOLDER} placeholder")

    data["version"] = version
    manifest.write_text(json.dumps(data, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Stamp the version given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the version to write, without a leading v")
    parser.add_argument("--manifest", type=pathlib.Path, default=MANIFEST)
    args = parser.parse_args(argv)

    stamp(args.manifest, args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
