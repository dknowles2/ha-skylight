"""Tests for the scripts the release workflow runs.

Nobody types a version any more, so these decide what gets published. The
increment logic in particular is the kind that breaks quietly — a lexical
comparison would put `2026.8.10` before `2026.8.2` and reuse a tag.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import set_version  # noqa: E402
from next_version import next_version  # noqa: E402


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


def test_next_version_reads_the_repository() -> None:
    """End to end, against this repository's real tags."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "next_version.py"), "--date", "2026-08-20"],
        capture_output=True,
        text=True,
        check=True,
    )
    # Releases have already been cut this month, so it cannot be the first.
    assert result.stdout.strip().startswith("2026.8.")
    assert result.stdout.strip() != "2026.8.0"


def test_set_version_writes_both_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """manifest.json and pyproject.toml have to agree, or CI stops the release."""
    manifest = tmp_path / "custom_components" / "skylight" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"domain": "skylight", "version": "2026.8.2"}) + "\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "ha-skylight"\nversion = "2026.8.2"\n\n'
        '[tool.ruff]\ntarget-version = "py314"\n'
    )

    monkeypatch.setattr(set_version, "MANIFEST", manifest)
    monkeypatch.setattr(set_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(sys, "argv", ["set_version.py", "2026.9.0"])

    assert set_version.main() == 0
    assert json.loads(manifest.read_text())["version"] == "2026.9.0"
    assert 'version = "2026.9.0"' in pyproject.read_text()
    # Only the project's own version moves; ruff's target stays put.
    assert 'target-version = "py314"' in pyproject.read_text()


def test_set_version_rejects_a_non_calver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad version must not reach the files, let alone a tag."""
    monkeypatch.setattr(sys, "argv", ["set_version.py", "1.2.3"])
    assert set_version.main() == 1
