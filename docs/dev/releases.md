---
title: Releases and maintenance branches
summary: How a release is cut from a tag, and how a patch for an older minor version is made on a release branch.
audience: dev
order: 74
covers:
  - .github/workflows/release.yml
  - .github/workflows/docker.yml
  - .github/workflows/ci.yml
  - CHANGELOG.md
---

# Releases and maintenance branches

All development, including the next major version, happens on `main`. Contributors
always open pull requests against `main`. A release is a tag; nothing else has to be
done by hand.

## Cutting a release

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add an
   empty `## [Unreleased]` above it. Merge that.
2. Tag the merged commit and push the tag:

   ```bash
   git tag vX.Y.Z <commit>
   git push origin vX.Y.Z
   ```

The tag starts two workflows:

- **`release.yml`** takes the `## [X.Y.Z]` section of `CHANGELOG.md` at the tagged
  commit as the release notes, publishes the GitHub release and opens a thread for it in
  the Announcements discussion category. With no such section it fails and publishes
  nothing.
- **`docker.yml`** builds the image, starts it and checks `/health`, then pushes
  `ghcr.io/antiv/mate` for `linux/amd64` and `linux/arm64`, tagged `X.Y.Z` and `X.Y`.

Both mark the release as the newest one (the GitHub release as *Latest*, the image as
`latest`) only if its tag is the highest final `vX.Y.Z` tag in the repository, by version
order. A patch for an older minor, or a pre-release such as `v2.0.0-rc1`, doesn't move
them.

## Patch releases

While `main` holds only fixes since the last release, a patch release is cut from `main`
like any other.

When `main` has moved on (new features for the next minor, or work for the next major)
and an older minor still needs a fix, the patch is made on a release branch:

1. **Create the branch once**, from the last tag of that minor:

   ```bash
   git switch -c release/1.5 v1.5.0
   git push origin release/1.5
   ```

2. **Fix on `main` first.** Merge the fix to `main` as usual, then bring the same commit
   to the release branch through a pull request:

   ```bash
   git switch -c fix-1.5-something origin/release/1.5
   git cherry-pick -x <commit on main>
   ```

   A fix never lands only on the release branch, so the next release from `main` can't
   lose it. `-x` records which `main` commit it came from.

3. **Changelog.** On the release branch, add a `## [1.5.1] - YYYY-MM-DD` section. Add the
   same section to `CHANGELOG.md` on `main`, between `[Unreleased]` and the newer
   releases, so `main` keeps the full history.

4. **Tag on the release branch**: `git tag v1.5.1 origin/release/1.5`, then push the tag.
   The workflows run as for any release. The image gets `1.5.1` and moves `1.5`, and
   `latest` stays where it is.

CI runs on pushes to `main` and to `release/**`, and on every pull request whatever its
base, so a pull request to `release/1.5` is tested like one to `main`. The monthly
dependency upgrade (`dependency-upgrade.yml`) only targets `main`. A release branch gets
a dependency bump only when one is cherry-picked to it, for example a security fix.

## Which versions get patches

Only the newest minor version gets fixes. Once 1.6.0 is out, fixes ship as 1.6.x, and
`release/1.5` gets no more commits unless a fix is too urgent to wait for users to
upgrade. Delete a release branch only when you are sure no more patches will come from it.
The tags keep every released state either way.
