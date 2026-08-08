# Releasing

Versions are `YYYY.M.N` — see [Versioning](../README.md#versioning). Releasing is running
one workflow; nothing is edited by hand and there is nothing to build.

## Cutting a release

1. Check CI on `main` is green.

2. Run the **Release** workflow from the Actions tab. Leave *version* blank and it takes
   the current year and month and the next `N` after the tags already published — so the
   release after `2026.8.2` is `2026.8.3`, and the first of September is `2026.9.0`. Fill
   it in only to override that.

   The workflow writes the version into `manifest.json` and `pyproject.toml`, commits that
   to `main`, tags it, and publishes the release with generated notes. The tag has to point
   at the commit carrying the version, because HACS installs the source tree at the tag —
   which is why the workflow commits before tagging rather than after.

3. Read the generated notes and edit them if a change deserves calling out — a breaking
   change, or anything needing action from someone upgrading. Nothing in the version
   number signals that, so the notes have to.

Bumping `pyskylight` is still a normal PR, and still has to move `requirements` in the
manifest and the pin in `pyproject.toml` together. Home Assistant installs the manifest pin
at setup, so a feature needing a newer library fails at runtime without it, and CI will not
catch that — the dev group is what tests run against.

## Why the version is not derived from the tag

`pyskylight` gets its version from the git tag at build time. This repository cannot:
`manifest.json` is read straight out of the source tree by Home Assistant and HACS, so the
number has to be committed. The workflow moves the editing off you rather than removing it.

## What people get

HACS offers the new version to anyone tracking the repository, and installs the tagged
source. Users who installed by copying `custom_components/skylight` by hand get nothing
automatically; they re-copy.

Without any release at all, HACS falls back to the default branch — every merge to `main`
reaching users unannounced. That is the situation a release exists to end, so once the
first tag is out, keep tagging.

## Fixing a bad release

Do not move or delete a published tag: HACS caches by version, and someone may already
have installed it. Cut the next point release instead — `2026.8.1` — even if it is minutes
later. The workflow refuses to reuse an existing tag for the same reason.
