# Releasing

Versions are `YYYY.M.N` — see [Versioning](../README.md#versioning). Releasing is running
one workflow; nothing is edited by hand and there is nothing to build.

## Cutting a release

1. Run the **Release** workflow from the Actions tab. Leave *version* blank and it takes
   the current year and month and the next `N` after the tags already published — so the
   release after `2026.8.2` is `2026.8.3`, and the first of September is `2026.9.0`. Fill
   it in only to override that.

   It writes the version into `manifest.json` and `pyproject.toml`, pushes a
   `release/<version>` branch, and opens a PR — or, if no `RELEASE_TOKEN` secret is set,
   prints a link for you to open it with. Either way the run's summary says which happened
   and what to do next.

2. Merge that PR once CI is green.

   Merging triggers the workflow's `tag` job, which tags the merge commit and publishes the
   release with generated notes. The tag has to point at a commit carrying the version,
   because HACS installs the source tree at the tag.

3. Read the generated notes and edit them if a change deserves calling out — a breaking
   change, or anything needing action from someone upgrading. Nothing in the version
   number signals that, so the notes have to.

### Why the PR needs a token

GitHub does not run workflows for a pull request opened with the workflow's own
`GITHUB_TOKEN`. It is a loop guard, and a sensible one — but it means a release PR opened
that way gets **no checks at all**, and `main` requires four of them, so the PR can never be
merged. The workflow reports success and leaves behind something stuck, which is worse than
not opening a PR in the first place.

So the PR is opened with `RELEASE_TOKEN`, a fine-grained personal access token, if one is
set. A PR opened with that is authored by a real account, and CI runs against it normally.

Creating it: **Settings → Developer settings → Personal access tokens → Fine-grained
tokens**, scoped to this repository, with **Contents: read** and **Pull requests: read and
write**. Add it under **Settings → Secrets and variables → Actions** as `RELEASE_TOKEN`.
Nothing else uses it, and the workflow does not need it for anything but the one API call.

Without the secret, the workflow still does everything else and finishes with a link to
open the PR yourself, which takes one click and produces the same result.

Note that the repository setting **Allow GitHub Actions to create and approve pull
requests** does not help here. It controls whether the workflow *may* open a PR at all;
whether that PR runs CI is a separate rule, and is the one that matters.

### Why it goes through a pull request

`main` is protected: it takes a PR, and the four CI jobs have to pass. That rule has no
bypass — GitHub does not allow granting one to the Actions app on a personal repository,
and granting one to the repository admin would have put back the hole the protection exists
to close.

So the workflow proposes rather than pushes. The upside is that the release commit gets
tested like any other change instead of landing straight on `main`.

The `tag` job runs on every push to `main` that touches the manifest, and does nothing
unless the version in it has no tag yet. Bumping the version in an ordinary PR therefore
releases it too, which is intended.

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
