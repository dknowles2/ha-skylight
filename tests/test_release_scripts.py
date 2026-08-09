"""Tests for the scripts around releasing.

`next_version.py` says which version to publish next, and the increment logic is
the kind that breaks quietly — a lexical comparison would put `2026.8.10` before
`2026.8.2` and reuse a tag.

`stamp_version.py` writes that version into the manifest at release time. The
repository carries a `0000.0.0` placeholder, because `hacs.json` sets
`zip_release` and HACS installs the zip the workflow attaches rather than the
source tree. A stamp that silently did nothing would ship that placeholder to
everyone.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import stamp_version  # noqa: E402
from next_version import next_version  # noqa: E402

CALVER = re.compile(r"^\d{4}\.(1[0-2]|[1-9])\.(0|[1-9]\d*)$")


@pytest.mark.parametrize(
    ("year", "month", "tags", "expected"),
    [
        # The first release of a month starts at 0, and N resets each month.
        (2027, 1, [], "2027.1.0"),
        (2026, 9, ["v2026.8.0", "v2026.8.2"], "2026.9.0"),
        (2026, 8, ["v2026.8.0", "v2026.8.1", "v2026.8.2"], "2026.8.3"),
        # Numeric, not lexical: "2026.8.10" > "2026.8.2".
        (2026, 8, ["v2026.8.2", "v2026.8.10"], "2026.8.11"),
        # Gaps do not renumber; the highest wins.
        (2026, 8, ["v2026.8.5"], "2026.8.6"),
        # A padded month is not the format we write, so it does not count.
        (2026, 8, ["v2026.08.1"], "2026.8.0"),
        # Neither does anything else in the tag namespace.
        (2026, 8, ["v1.2.3", "not-a-tag", "v2026.8"], "2026.8.0"),
    ],
)
def test_next_version(year: int, month: int, tags: list[str], expected: str) -> None:
    """The next point release for a month, from the tags already published."""
    assert next_version(year, month, tags) == expected


def test_next_version_runs_end_to_end() -> None:
    """The script itself works, whatever tags happen to be present.

    It deliberately does not assert a particular answer from the repository's
    own tags: CI checks out shallow, so there are none, and the script rightly
    says `2026.8.0`. The release workflow uses `fetch-depth: 0` for that reason,
    and refuses to reuse an existing tag if it ever gets it wrong.
    """
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "next_version.py"),
            "--date",
            "2026-08-20",
            "--tags",
            "v2026.8.0,v2026.8.2",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "2026.8.3"


def test_next_version_reads_git_when_no_tags_are_given() -> None:
    """Without `--tags` it asks git, which must at least not blow up."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "next_version.py"), "--date", "2026-08-20"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert CALVER.match(result.stdout.strip())


def test_next_version_reads_this_repository_wherever_it_is_run(tmp_path: Path) -> None:
    """The tags come from the script's own repository, not the working directory.

    Run from a sibling checkout it used to answer `2026.8.0`, because no tag
    there matches `vYYYY.M.N` — a number that looks like a fresh month and would
    be published over a tag that already exists.
    """
    from_repo = subprocess.run(
        [sys.executable, str(SCRIPTS / "next_version.py"), "--date", "2026-08-20"],
        capture_output=True,
        text=True,
        check=True,
        cwd=SCRIPTS.parent,
    )
    from_elsewhere = subprocess.run(
        [sys.executable, str(SCRIPTS / "next_version.py"), "--date", "2026-08-20"],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )

    assert from_elsewhere.stdout == from_repo.stdout


def _manifest(tmp_path: Path, version: str) -> Path:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"domain": "skylight", "version": version}) + "\n")
    return manifest


def test_stamp_version_replaces_the_placeholder(tmp_path: Path) -> None:
    """The released version reaches the manifest that gets zipped."""
    manifest = _manifest(tmp_path, stamp_version.PLACEHOLDER)

    stamp_version.stamp(manifest, "2026.9.0")

    assert json.loads(manifest.read_text())["version"] == "2026.9.0"


def test_stamp_version_keeps_the_rest_of_the_manifest(tmp_path: Path) -> None:
    """hassfest validates every other key, so none of them may be disturbed."""
    manifest = _manifest(tmp_path, stamp_version.PLACEHOLDER)

    stamp_version.stamp(manifest, "2026.9.0")

    assert json.loads(manifest.read_text())["domain"] == "skylight"


def test_stamp_version_refuses_a_stamped_manifest(tmp_path: Path) -> None:
    """Running twice means the checkout is not what this expects."""
    manifest = _manifest(tmp_path, "2026.8.4")

    with pytest.raises(SystemExit, match=r"expected the 0000\.0\.0 placeholder"):
        stamp_version.stamp(manifest, "2026.9.0")


@pytest.mark.parametrize("version", ["1.2.3", "v2026.9.0", "2026.13.0", "2026.9", "", "latest"])
def test_stamp_version_rejects_a_non_calver(tmp_path: Path, version: str) -> None:
    """A tag of the wrong shape would be shipped verbatim to everyone."""
    manifest = _manifest(tmp_path, stamp_version.PLACEHOLDER)

    with pytest.raises(SystemExit, match=r"not a YYYY\.M\.N version"):
        stamp_version.stamp(manifest, version)

    # Nothing is written on the way to refusing.
    assert json.loads(manifest.read_text())["version"] == stamp_version.PLACEHOLDER


def test_the_repository_carries_the_placeholder() -> None:
    """The whole scheme rests on this, and a stray edit would break it quietly."""
    manifest = Path(__file__).parent.parent / "custom_components" / "skylight" / "manifest.json"

    assert json.loads(manifest.read_text())["version"] == stamp_version.PLACEHOLDER
