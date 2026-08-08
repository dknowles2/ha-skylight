#!/usr/bin/env python3
"""Work out the next calendar version, from today's date and the existing tags.

Versions are `YYYY.M.N` — year, unpadded month, and a point release counting
from 0 within that month. This looks at the `vYYYY.M.N` tags already in the
repository and returns the next N for the current month, so the release
workflow never needs a version typed by hand.

    $ python3 scripts/next_version.py
    2026.8.3

`--date` and `--tags` exist for the tests; without them the date is today in
UTC and the tags come from git.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import UTC, datetime

TAG = re.compile(r"^v(\d{4})\.(1[0-2]|[1-9])\.(0|[1-9]\d*)$")


def next_version(year: int, month: int, tags: list[str]) -> str:
    """Return the next `YYYY.M.N` for the given month."""
    used = [
        int(match.group(3))
        for tag in tags
        if (match := TAG.match(tag.strip()))
        and int(match.group(1)) == year
        and int(match.group(2)) == month
    ]
    # First release of a month starts at 0; N resets each month.
    return f"{year}.{month}.{max(used) + 1 if used else 0}"


def git_tags() -> list[str]:
    """Return every tag in the repository."""
    result = subprocess.run(["git", "tag", "--list"], capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def main() -> int:
    """Print the next version."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD to release as; defaults to today, UTC")
    parser.add_argument("--tags", help="comma-separated tags to consider; defaults to git")
    args = parser.parse_args()

    when = (
        datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=UTC)
        if args.date
        else datetime.now(UTC)
    )
    tags = args.tags.split(",") if args.tags is not None else git_tags()
    print(next_version(when.year, when.month, tags))
    return 0


if __name__ == "__main__":
    sys.exit(main())
