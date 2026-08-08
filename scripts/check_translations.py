#!/usr/bin/env python3
"""Fail if translations/en.json has drifted from strings.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "skylight"


def main() -> int:
    """Compare the two files and report any difference."""
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    if strings == english:
        return 0
    print(
        "translations/en.json is out of sync with strings.json.\n"
        "Copy it across:\n"
        "  cp custom_components/skylight/strings.json "
        "custom_components/skylight/translations/en.json",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
