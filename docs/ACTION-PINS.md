# Pinned actions, and the advisory check for each

Every GitHub Action in `.github/workflows/` names a commit SHA. This file is the
record that makes those pins safe rather than a silent freeze: it says which
version each SHA is, when it was resolved, what was asked of the advisory
database, and what came back.

A recorded negative is the point. "No advisory" with no query and no date beside
it is indistinguishable from nobody having looked.

## Resolution

- **Date:** 2026-09-10
- **Resolution query:** `GET /repos/{owner}/{repo}/commits/{ref}` — resolves a
  lightweight tag, an annotated tag, or a branch head the same way
- **Advisory query:** `GET /advisories?ecosystem=actions&affects={owner}/{repo}`,
  then `GET /advisories/{ghsa_id}` for the affected version range
- **Before this change, how many of the six were already pinned to a SHA:**
  **none.** Five were on mutable major-version tags and one on a branch.

## The six

| Action | Was | Now | Tag at that SHA |
|---|---|---|---|
| `actions/checkout` | `v4` | `11d5960a326750d5838078e36cf38b85af677262` | v4.4.0 |
| `actions/setup-python` | `v5` | `a26af69be951a213d495a4c3e4e4022e16d87065` | v5.6.0 |
| `actions/upload-artifact` | `v4` | `ea165f8d65b6e75b540449e92b4886f43607fa02` | v4.6.2 |
| `actions/download-artifact` | `v4` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` | v4.3.0 |
| `softprops/action-gh-release` | `v2` | `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` | v2.6.2 |
| `pypa/gh-action-pypi-publish` | `release/v1` **(a branch)** | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` | v1.14.2 |

## Advisory check, per SHA

Four of the six returned no advisory at all. Two returned one each, and both
were checked against the exact version being pinned rather than against the
action in general — which is the distinction that decides whether a pin is safe.

| Action | Advisory | Severity | Vulnerable range | First patched | Pinned version | Affected? |
|---|---|---|---|---|---|---|
| `actions/checkout` | none | — | — | — | v4.4.0 | no |
| `actions/setup-python` | none | — | — | — | v5.6.0 | no |
| `actions/upload-artifact` | none | — | — | — | v4.6.2 | no |
| `actions/download-artifact` | [GHSA-cxww-7g56-2vh6](https://github.com/advisories/GHSA-cxww-7g56-2vh6) | high | `>= 4.0.0, < 4.1.3` | 4.1.3 | v4.3.0 | **no** — past the range |
| `softprops/action-gh-release` | none | — | — | — | v2.6.2 | no |
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
