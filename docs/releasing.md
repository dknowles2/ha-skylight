# Releasing

Versions are `YYYY.M.N` — see [Versioning](../README.md#versioning).

## Cutting a release

1. Open **Releases**. A draft is already there: Release Drafter has been adding every pull
   request as it merged, grouped by the `feat:` / `fix:` / `docs:` prefix in its title.

2. Set the tag. **Do not trust the drafted name** — Release Drafter counts, and versions
   here are dates, so it is right within a month and wrong the moment the month turns.
   This is the one thing typed by hand:

   ```
   uv run python scripts/next_version.py
   ```

   Run it with `uv`, not a bare `python3`: the script is written against the Python this
   project targets, and a system interpreter is often older.

   prints the right one — the current year and month, and the next `N` after the tags
   already published. Prefix it with `v`.

3. Read the notes, and edit them if a change deserves calling out — a breaking change, or
   anything needing action from someone upgrading. Nothing in the version number signals
   that, so the notes have to.

4. Publish. The **Release** workflow stamps the version into `manifest.json`, zips the
   integration, signs the zip with sigstore, and attaches it to the release. HACS installs
   that zip.

Nothing is committed, and nothing touches `main`.

## Why the version is not in the repository

`manifest.json` in the repository carries `0000.0.0`, and the real version only ever exists
inside a released zip.

The alternative is committing it, which is what this repository used to do — and because
`main` is protected, that meant a release branch, a pull request, and a workflow that could
open one. That last part is where it fell down: GitHub does not run workflows for a pull
request opened with the workflow's own `GITHUB_TOKEN`, so the release PR got no checks,
`main`'s required checks could never pass, and the PR was unmergeable. The workarounds were
a personal access token to store and rotate, or a manual step every release.

Shipping a zip removes the problem rather than working around it. `hacs.json` sets
`zip_release`, HACS downloads the attached archive instead of the source tree, and the
version is stamped in at release time by `scripts/stamp_version.py`.

`stamp_version.py` refuses to run against a manifest that already holds a real version, so
a checkout that is not what it expects fails loudly rather than shipping something odd.
There is a test asserting the repository still carries the placeholder, since a stray edit
would otherwise break this quietly.

## What this means for manual installs

Copying `custom_components/skylight/` out of the repository gives you an integration whose
version reads `0000.0.0`. Home Assistant will run it, but it will not know what it is
running, and neither will you when reporting a problem.

Download `skylight.zip` from the release instead and unpack it into
`config/custom_components/skylight/`. The README says so too.

## Verifying a release

The zip is signed with [sigstore](https://www.sigstore.dev/), and the signature is attached
to the release beside it:

```bash
uvx sigstore verify identity skylight.zip \
  --cert-identity-regexp 'https://github.com/dknowles2/ha-skylight/.*' \
  --cert-oidc-issuer https://token.actions.githubusercontent.com
```

There is no signing key anywhere: sigstore uses the workflow's OIDC identity, so what the
signature proves is that this zip was built by that workflow in this repository.
