# How work happens in this repository

Agreed by the Board on 2026-09-10. This file is the record, so the process does
not live only in a conversation.

---

## Priority is a number, and the number is the rank

Lower is more critical. Every issue carries exactly one.

| Label | What it means | The test for it |
|---|---|---|
| **P1** | Cadre cannot do its core job. A firm cannot operate, or nobody can install Cadre. | If this stays broken, the product does not work for anyone. |
| **P2** | The core works, but a documented promise is false or a supported path is broken. | Something we say Cadre does, it does not do, or only does in some firms. |
| **P3** | It works, but it is wrong, stale or misleading to the operator. | Nothing breaks, but a reader is told something untrue. |
| **P4** | An enhancement or a nice to have. Core function is unaffected. | Cadre is fine without it. It is better with it. |

Alongside the rank:

- `type:` says what kind of work it is — `defect`, `packaging`, `docs`, `infra`.
- `area:` says which part of Cadre it touches — `members`, `base`, `dashboard`,
  `founding`, `boardroom`, `pulse`.
- `needs:board` marks anything blocked on a decision only the Board can make.

Rank is about consequence, not effort. A one-line fix that stops anyone
installing Cadre is P1. A large, careful improvement to something that already
works is P4.

---

## How a change reaches main

Every change happens on a branch and reaches `main` through a pull request.
Nothing is committed to `main` directly.

| Who | Does |
|---|---|
| **Builder** | Owns one lane. Works in its own git worktree with its own virtual environment. Commits small, with clear messages, to its own branch. Never pushes. Never touches `main`. Never opens a pull request. |
| **Orchestrator** | Pushes the branch, opens the pull request, commissions the verification, and is **the only account that merges into `main`**. |
| **Verifier** | A separate session that takes a pull request and tries to break it. It does not fix anything. It reports what it found. |
| **Board** | Decides anything marked `needs:board`, and anything that costs money or changes what Cadre promises. |

The loop is: builder reaches green, orchestrator opens the pull request,
verifier stress tests it and reports, findings go back to the builder, and it
round-trips until the build is solid. A builder saying "done" is a claim. The
verifier's report is the evidence.

### Branch naming

- `lane/<subject>` — a builder's lane of work.
- `infra/<subject>` — repository, pipeline and process work.
- `fix/<subject>` — a single small correction.

---

## What the pipeline proves

**`tests.yml`** runs on every pull request and on pushes to `main`, `feat/`,
`lane/` and `infra/` branches.

- The suite on Linux, macOS and Windows. Windows runs the portable core while
  the POSIX-flavoured shell and hook tests are ported.
- **A clean install from the built wheel.** This job builds the wheel, installs
  it into an empty environment, proves the package imports from that install,
  proves both console scripts resolve, runs the suite against the installed
  package, and initializes a firm with it.

That last job exists because of a real failure. A dependency declared with no
upper bound resolved to a new major version that removed a module Cadre
imports. Every development environment stayed green, because each had frozen
the working version months earlier, while `pip install cadre` was broken for
anyone starting fresh. **An editable install in a warm environment cannot see
that class of break. A wheel installed into an empty environment can.**

**`release.yml`** fires only on a version tag. It builds the wheel and source
distribution, proves the wheel installs and imports from an empty environment,
checks the tag matches the packaged version, and attaches both files to a
GitHub Release. Publishing to PyPI is written but stays skipped until the
repository has the credentials for it, so pushing a tag can never publish by
accident.

### Two rules for anything added to the pipeline

1. **No step that rewrites files and exits zero.** A formatter or linter run in
   fix mode inside a required check is a check that cannot fail. If a check
   cannot fail, it is decoration.
2. **A search that finds nothing must be able to tell absent from blind.** Any
   check that passes by finding no problems needs a control proving it can
   still find one.

---

## Two deliberate omissions, and why

**Branch protection requires status checks, not an approving review.** GitHub
refuses an approval on a pull request opened by the same account. Since the
orchestrator opens and merges every pull request, requiring a review would
deadlock all of them. The verifier's report, recorded on the pull request, is
the review.

**Actions are pinned to major version tags, not to commits.** Pinning to a
commit is the stricter practice, but a commit pin also stops receiving security
patches, so it is only safe once someone has checked the advisories for the
commit being pinned. That check has not been done, and pinning without it would
trade one risk for a quieter one. Tracked as its own issue rather than done
half way.
