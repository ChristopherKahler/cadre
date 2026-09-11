# Pinned actions, and the advisory check for each

Every GitHub Action in `.github/workflows/` names a commit SHA. This file is the
record that makes those pins safe rather than a silent freeze: it says which
version each SHA is, when it was resolved, what was asked of the advisory
database, and what came back.

A recorded negative is the point. "No advisory" with no query and no date beside
it is indistinguishable from nobody having looked.

## Resolution

- **Date:** 2026-09-11
- **Resolution query:** `GET /repos/{owner}/{repo}/commits/{ref}` — resolves a
  lightweight tag, an annotated tag, or a branch head the same way
- **Advisory query:** `GET /advisories?ecosystem=actions&affects={owner}/{repo}`,
  then `GET /advisories/{ghsa_id}` for the affected version range
- **Before this change, how many of the six were already pinned to a SHA:**
  **none.** Five were on mutable major-version tags and one on a branch.

## The six

| Action | Before pinning (2026-09-10) | Now | Tag at that SHA |
|---|---|---|---|
| `actions/checkout` | `v4` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | v7.0.1 |
| `actions/setup-python` | `v5` | `5fda3b95a4ea91299a34e894583c3862153e4b97` | v7.0.0 |
| `actions/upload-artifact` | `v4` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | v7.0.1 |
| `actions/download-artifact` | `v4` | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | v8.0.1 |
| `softprops/action-gh-release` | `v2` | `efb35369e0ad2afab669f228072c1b0d510eae64` | v3.0.3 |
| `pypa/gh-action-pypi-publish` | `release/v1` **(a branch)** | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` | v1.14.2 |

## Advisory check, per SHA

Four of the six returned no advisory at all. Two returned one each, and both
were checked against the exact version being pinned rather than against the
action in general — which is the distinction that decides whether a pin is safe.

| Action | Advisory | Severity | Vulnerable range | First patched | Pinned version | Affected? |
|---|---|---|---|---|---|---|
| `actions/checkout` | none | — | — | — | v7.0.1 | no |
| `actions/setup-python` | none | — | — | — | v7.0.0 | no |
| `actions/upload-artifact` | none | — | — | — | v7.0.1 | no |
| `actions/download-artifact` | [GHSA-cxww-7g56-2vh6](https://github.com/advisories/GHSA-cxww-7g56-2vh6) | high | `>= 4.0.0, < 4.1.3` | 4.1.3 | v8.0.1 | **no** — past the range |
| `softprops/action-gh-release` | none | — | — | — | v3.0.3 | no |
| `pypa/gh-action-pypi-publish` | [GHSA-vxmw-7h4f-hqxh](https://github.com/advisories/GHSA-vxmw-7h4f-hqxh) | low | `< 1.13.0` | 1.13.0 | v1.14.2 | **no** — past the range |

`GHSA-cxww-7g56-2vh6` is arbitrary file write via artifact extraction.
`GHSA-vxmw-7h4f-hqxh` is injectable expression expansion in action steps. Both
are fixed in versions older than the ones pinned here, so pinning does not
freeze this repository on either.

## Why the PyPI one was singled out

`pypa/gh-action-pypi-publish` was on `release/v1`, a **branch**, which moves on
every commit its maintainer pushes — looser even than the mutable tags the other
five were on. It is also the action with the most consequential permission in
the repository: it runs with `id-token: write` and is the step that publishes
under this project's name. Publishing cannot be undone.

Nothing could fire today — the publish job is gated on a repository variable
that is not set, and on a tag ref — so this was about what is armed for the day
the switch is flipped, not a live exposure. Tracked as #33.

## Keeping them current

A commit pin does not update itself. Two mechanisms, because one is not enough:

1. **Dependabot**, configured in `.github/dependabot.yml` for the
   `github-actions` ecosystem, weekly. It understands SHA pins and opens pull
   requests that move them, with the release notes attached.
2. **`scripts/check-action-pins.py`**, run by the `action-pins` job in
   `tests.yml` on every push to a watched branch and on every pull request. It
   fails if any reference stops being a 40-character SHA with its tag beside it,
   so a hand-edit back to `@v4` is caught rather than merged.

When a pin moves, update the two tables above in the same pull request. That is
what makes the next reader able to trust them.
