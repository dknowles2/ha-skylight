# Releasing

Versions are `YYYY.M.N` — see [Versioning](../README.md#versioning). Releasing is just
tagging: HACS installs from the newest GitHub release, and there is nothing to build.

## Cutting a release

1. Work out the version. Current year and month, and `N` counting from 0 within that
   month — so the first release of August 2026 is `2026.8.0` and the next is `2026.8.1`.
   `N` resets each month.

2. Bump both files and merge that through a PR like any other change:

   - `custom_components/skylight/manifest.json` — `version`
   - `pyproject.toml` — `version`

   Also bump `requirements` in the manifest and the `pyskylight` pin in `pyproject.toml`
   together if the release depends on a new `pyskylight`. Home Assistant installs the
   manifest pin at setup, so a feature that needs a newer library will fail at runtime
   without it, and CI will not catch that — the dev group is what tests run against.

3. Wait for CI on `main` to pass.

4. Tag the merge commit and push:

   ```bash
   git checkout main && git pull && git tag v2026.8.0 && git push origin v2026.8.0
   ```

   The tag is the version with a `v` prefix. The release workflow verifies it names the
   version in `manifest.json` and refuses to publish otherwise, then creates the GitHub
   release with generated notes.

5. Read the generated notes and edit them if a change deserves calling out — a breaking
   change, or anything needing action from someone upgrading. Nothing in the version
   number signals that, so the notes have to.

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
later.
