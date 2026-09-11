#!/usr/bin/env python3
"""End-to-end acceptance: can a real user install Cadre and USE it, on this OS?

Run it on Linux, macOS or Windows. It builds a wheel, installs it into an empty
virtual environment, and drives the actual user journey against the INSTALLED
package — never against the source tree. It prints a scoreboard, so a run that
is not all-green still tells you exactly how far the product gets today.

    python scripts/acceptance-e2e.py
    python scripts/acceptance-e2e.py --keep          # leave the sandbox behind
    python scripts/acceptance-e2e.py --json out.json # machine-readable summary

Every status is one of:

    PASS     the step did the thing and the evidence was read back
    FAIL     the step should work on this OS and did not
    BLOCKED  a known open defect stops it; the reason names the defect
    SKIP     not applicable on this OS

The --json summary carries a "completed" flag, and it is true only when the run
reached the end of its own report. That summary is written from a finally: block,
so a harness that dies at row 1 of 26 still leaves a file behind -- and until
2026-09-10 that file said "fail": 0, which is the same value it carries when
nothing failed. Measured: an exception injected after the first row produced exit
1 and a summary reading 1 pass, 0 fail, 0 blocked, 0 skip, one row. Nothing in it
said the run had stopped early. Reading the row count instead is not a fix, since
that only tells you anything if you already know what the total should be.

Anything consuming this file -- a dashboard, a later script, a person comparing
two runs -- must check "completed" before believing a count.
scripts/grade-acceptance.py does, and it is what CI grades.

WHY THIS EXISTS, and why several steps look paranoid: on 2026-09-10 the CI
board had been red on macOS and Windows for a month while every local check
said green, because the Windows job ran a ``-k`` filter that deselected 1081 of
1321 tests and nobody compared the two numbers. Separately, an ad-hoc Windows
probe reported two product failures that were really a missing directory in the
probe's own copy of the tree. So this harness carries CONTROLS: steps that
prove the harness can see what it claims to be measuring, and negative arms
that must fail. A green step with no control is not evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IS_WIN = sys.platform.startswith("win")
TIMEOUT = 900

PASS, FAIL, BLOCKED, SKIP = "PASS", "FAIL", "BLOCKED", "SKIP"

# The base section's rows, by name, in the order the executed path emits them.
#
# WHY A LIST AND NOT A COUNT. This section has one arm that runs eighteen rows
# and several that run none, and the skipped arms used to record NOTHING -- the
# rows did not fail, they ceased to exist. A scoreboard that shrinks reports a
# smaller run as a cleaner one, and a row that is absent says nothing at all,
# while a SKIP row says "this host could not check it, and here is why". The two
# are not close.
#
# A count would rot the first time someone added a row. Naming them means the
# skipped path can emit exactly the rows the executed path would have, and
# `Board.assert_section` can then require the executed path to emit exactly this
# list -- so the list and the code cannot drift apart without a FAIL row saying
# which name moved.
BASE_SECTION_ROWS = [
    "base is installed on this host",
    "the resolved base matches this platform",
    "the base bridge module loads from the installed package",
    "base reads the firm's own workspace tier, not the operator's",
    "the Cadre manifest installs into base and is read back",
    "the extension's command RUNS through base, not merely installs",
    "the firm's base domain carries a live rule",
    "the demo firm has a Unit to close",
    "two Members of one firm get different briefings",
    "a Board session is briefed as nobody (scope control)",
    "the write-back gate lets a Member with nothing owed finish",
    "closing a Unit with nothing recorded blocks the session",
    "the other Member is not blocked by that debt (gate control)",
    "the firm's graph starts empty (evidence control)",
    "a Member's lesson reaches the graph and is readable back out",
    "the isolation digest can actually detect a change",
    "the other platform's tier is being watched",
    "the append scan can actually detect a planted write",
    "the append scan ignores another session's writes",
    "the operator's own graph and registry were never written to",
    "no appended write carries this run's fingerprint",
]

# Everything before the base section, in the order the executed path emits it.
#
# WHY THIS EXISTS. Two steps used to `return b.report()` outright -- the wheel
# build and the pip install. Both emit a FAIL row first, so nothing read green,
# but both dropped every row after them and returned with `finished` still
# False. That is the shrinking scoreboard BASE_SECTION_ROWS was written to stop,
# one level up from where it was being stopped.
#
# A LIST ALONE DOES NOT CLOSE THEM, and that is worth knowing before editing it.
# `assert_section` compares the emitted names to the declared names AS A
# SEQUENCE, and `skip_rest` appends what is missing at the END. So a declared
# row that did not fire mid-run lands after the rows that came after it and the
# order check fails even though every name is present. The base section gets
# away with it because every one of its `skip_rest` calls happens AT the branch
# point, before any later row exists. The arms below give this half the same
# property: each branch fills in the rows its sibling would have emitted, right
# where they belong.
def console_script_row(exe: str) -> str:
    """The one place this row's name is spelled.

    It used to be spelled twice: once as a literal in the declared list and once
    in an f-string at the emit site. Two spellings of a name that must match
    exactly is a drift waiting to happen, and the only thing that would notice
    is `assert_section` failing on somebody's CI leg with a name mismatch and no
    clue which spelling moved.
    """
    return f"console script `{exe}` resolves"


CONSOLE_SCRIPTS = ("cadre", "firm")

PRE_BASE_ROWS = [
    "wheel provided",
    "build a wheel from source",
    "create an empty virtual environment",
    "pip install the wheel into it",
    "the mcp dependency resolved under its upper bound",
    *(console_script_row(e) for e in CONSOLE_SCRIPTS),
    "every module imports from the installed package",
    "CONTROL: firm resolves from the venv, not from the source tree",
    "the MCP tool surface loads from the installed package",
    "the wheel carries its data files, not only its code",
    "CONTROL: no firm database exists before init",
    "cadre init --demo creates a firm",
    "the demo firm actually has a roster and work",
    "cadre init --install-hooks wires the session hook",
    "the session hook command can be read back out of settings.json",
    "the session hook AS REGISTERED actually emits the roster",
    "RED ARM: the recorded interpreter path exists to be removed",
    "RED ARM: with the recorded path removed the hook goes quiet and says why",
    "the registered hook emits the roster without a developer editable install "
    "helping it",
    "re-running init is idempotent, not destructive",
    "a member exists to act as",
    "a Member can ask the Board for approval (firm gate request)",
    "a Member can escalate to the Board (firm escalation raise)",
    "the host scheduler for this OS resolves and is available",
    "NEGATIVE CONTROL: a nonexistent runtime is refused",
    "a native executable is accepted as the Member runtime",
]

# The whole scoreboard, which is what `end_run` conserves.
HARNESS_ROWS = PRE_BASE_ROWS + BASE_SECTION_ROWS


class Board:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.t0 = time.time()

    def add(self, status: str, name: str, detail: str = "") -> str:
        self.rows.append({"status": status, "name": name, "detail": detail})
        mark = {PASS: "PASS   ", FAIL: "FAIL   ", BLOCKED: "BLOCKED", SKIP: "SKIP   "}[status]
        print(f"  {mark}  {name}")
        for line in str(detail).splitlines():
            if line.strip():
                print("           " + line[:150])
        return status

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r["status"] == status)

    def skip_rest(self, names: list[str], detail: str) -> None:
        """Record SKIP for every declared row this section has not reached.

        Called on each path that stops the section early, with the reason that
        path stopped. The rows are emitted in declared order, so a skipped run
        and a full run put the same names on the scoreboard in the same places
        and the two are directly comparable.
        """
        done = {r["name"] for r in self.rows}
        for name in names:
            if name not in done:
                self.add(SKIP, name, detail)

    def assert_section(self, start: int, names: list[str], section: str) -> None:
        """Require the rows emitted since `start` to be exactly `names`.

        This is the half that keeps the declared list honest. Without it the
        list is a comment: delete a `b.add` and the executed path quietly emits
        seventeen rows where it declares eighteen, which is the same
        disappearance the list exists to prevent, one level up. Both directions
        are covered -- a name in the code but not the list is drift too.
        """
        emitted = [r["name"] for r in self.rows[start:]]
        if emitted == names:
            return
        missing = [n for n in names if n not in emitted]
        extra = [n for n in emitted if n not in names]
        dupes = sorted({n for n in emitted if emitted.count(n) > 1})
        why = []
        if missing:
            why.append("DECLARED BUT NEVER EMITTED: " + "; ".join(missing))
        if extra:
            why.append("EMITTED BUT NOT DECLARED: " + "; ".join(extra))
        if dupes:
            why.append("EMITTED TWICE: " + "; ".join(dupes))
        if not why:
            why.append(f"same {len(names)} names in a different order; the list "
                       "is the spec, so reorder the list or the code")
        self.add(FAIL, f"the {section} section recorded every row it declares",
                 f"declared {len(names)}, emitted {len(emitted)}\n" + "\n".join(why))

    def report(self) -> int:
        print()
        print("=" * 78)
        print("SCOREBOARD")
        print("=" * 78)
        for r in self.rows:
            print(f"  {r['status']:<8} {r['name']}")
        print("-" * 78)
        print(f"  {self.count(PASS)} pass · {self.count(FAIL)} fail · "
              f"{self.count(BLOCKED)} blocked · {self.count(SKIP)} skip "
              f"· {time.time() - self.t0:.0f}s")
        # USABLE is a claim about what was PROVEN, so a skip has to change the
        # headline and not merely add a footnote. A run that skipped the whole
        # base section proved 23 rows and left every base row unmeasured;
        # printing USABLE there is a verdict that reads green over the thing it
        # names. NOT ESTABLISHED is not a failure — it says this host could not
        # check it, which is the truth.
        if self.count(FAIL):
            print()
            print("  NOT USABLE END TO END on this OS. The FAIL rows are "
                  "things that should work here and do not.")
        elif self.count(BLOCKED):
            print()
            print("  Usable as far as it goes, with known defects blocking the "
                  "BLOCKED rows. Each names its defect.")
        elif self.count(SKIP):
            print()
            print(f"  NOT ESTABLISHED on this OS: {self.count(PASS)} row(s) "
                  f"passed and {self.count(SKIP)} were SKIPPED, so the skipped "
                  "ground is unmeasured rather than proven. Each SKIP row says "
                  "what this host could not check and why.")
        else:
            print()
            print(f"  USABLE END TO END on this OS. All {self.count(PASS)} "
                  "rows measured, none skipped.")
        # THE EXIT CODE IS THE HALF CI AND THE NEXT VERIFIER ACTUALLY READ.
        # The headline above already refuses to say USABLE over a skipped
        # section; returning 0 there said it again in the one place a script
        # can see. A run that proved nothing is not a clean run.
        #
        #   0  every row measured, nothing skipped
        #   1  at least one FAIL -- something that should work here does not
        #   2  no FAIL, but rows were SKIPPED, so this host did not establish
        #      the skipped ground. Not a failure; not a pass either.
        #   4  no FAIL, but a known defect BLOCKED rows. Not a failure either,
        #      and emphatically not a clean run.
        #
        # 2 and 4 are distinct from 1 on purpose: scripts/grade-acceptance.py
        # needs to tell "the harness died" from "the harness declined to
        # measure" from "a known defect stopped it", and a single non-zero
        # cannot carry that. 3 is the isolation refusal, taken already.
        #
        # 4 EXISTS BECAUSE THIS RETURNED 0. The headline four lines above has
        # always ranked BLOCKED between FAIL and SKIP and printed "Usable as far
        # as it goes, with known defects blocking the BLOCKED rows" -- while the
        # exit code said 0, which this same comment block defines as "every row
        # measured, nothing skipped". That is not what happened, and the exit
        # code is the half CI and grade-acceptance.py actually read. The
        # ordering below follows the headline's own precedence rather than
        # inventing a second one.
        #
        # This is NOT a red build. grade-acceptance.py stays green on 4, for
        # the reason it is green on a BLOCKED row today: a BLOCKED row names an
        # open defect that is already tracked, and failing the build here
        # teaches the next person to delete the row instead of the defect.
        if self.count(FAIL):
            return 1
        if self.count(BLOCKED):
            return 4
        if self.count(SKIP):
            return 2
        return 0


def run(argv: list[str], *, cwd: Path | None = None, env: dict | None = None,
        timeout: int = TIMEOUT, stdin_text: str | None = None) -> tuple[int, str]:
    """Run a command with an EXPLICIT environment and a timeout.

    Explicit env, never ambient inheritance: a child spawned from a
    differently-launched parent inherits a different PATH and fails in ways
    that look like product bugs. Bytes decoded here rather than text=True,
    because text=True decodes as cp1252 on Windows and dies on any non-ASCII
    output.
    """
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(
            argv, cwd=str(cwd) if cwd else None, env=e,
            capture_output=True, timeout=timeout,
            # Bytes, never text=True: on Windows text mode translates newlines
            # on the WRITE side, so a JSON payload arrives altered.
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
        )
    except OSError as exc:
        # EVERY way the OS can refuse to start the process, under ONE message.
        #
        # This caught FileNotFoundError alone, so a `base` that exists and
        # cannot be executed -- wrong permission bits, a directory of that
        # name, a dangling interpreter on the shebang line -- raised
        # PermissionError straight out of run() and killed the harness with a
        # traceback. Measured 2026-09-11: EACCES at the base section's very
        # first call took the run down, so the eighteen rows of that section
        # were not SKIPPED, they were never reached, and the summary said 23
        # rows with completed=false. That is the row-conservation defect
        # arriving through a door the declared row list cannot close, because
        # no row runs at all.
        #
        # ONE MESSAGE, NOT TWO, and that is the second half of the fix. The
        # first attempt kept a separate "not found:" arm for
        # FileNotFoundError, and the same missing command then produced
        # different text on different systems: EACCES on Linux for a directory
        # named `base`, but ENOENT on Windows, where the loader searches
        # PATHEXT and never finds an executable of that name at all. So the
        # harness said "could not be run" on one OS and "not found" on the
        # other for the identical host condition. Caught by CI on
        # windows-latest, which is exactly what the three-OS matrix is for.
        #
        # The distinction was never worth keeping: both mean "this host cannot
        # start that command", both are answered with 127, and the OS's own
        # errno text is appended, so nothing diagnostic is lost. What IS worth
        # keeping is that an operator comparing two runs on two machines reads
        # the same sentence for the same fact.
        #
        # 127 is the right code -- it is what the base section's first arm
        # already knows how to handle: SKIP all eighteen rows and say why.
        return 127, f"could not be run: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    out = (p.stdout or b"").decode("utf-8", errors="replace")
    err = (p.stderr or b"").decode("utf-8", errors="replace")
    return p.returncode, out + err


def _registered_session_hook_command(settings: Path) -> str | None:
    """The SessionStart command string as `cadre init` actually wrote it.

    Read back from disk rather than reconstructed, because the point is to test
    what was wired, not what we believe was wired.
    """
    try:
        data = json.loads(settings.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    for entry in (data.get("hooks") or {}).get("SessionStart") or []:
        for h in entry.get("hooks") or [entry]:
            cmd = h.get("command") or ""
            if "cadre-session-pulse" in cmd:
                return cmd
    return None


def _expand_hook_command(command: str, ws: Path) -> list[str]:
    """Turn the registered command string into an argv, expanding the one
    variable Claude Code substitutes.

    `$CLAUDE_PROJECT_DIR` is expanded by the harness because Claude Code
    expands it at invocation; leaving it literal would test a path that does
    not exist and blame the hook for it. Nothing else is expanded — an
    interpreter name stays exactly as registered, which is the thing under test.
    """
    expanded = command.replace("$CLAUDE_PROJECT_DIR", str(ws))
    expanded = expanded.replace("${CLAUDE_PROJECT_DIR}", str(ws))
    return shlex.split(expanded, posix=not IS_WIN)


def binary_kind(path: str | None) -> str:
    """Classify an executable by its MAGIC BYTES, never by its name.

    The Windows base sits at /mnt/c/Users/<user>/.local/bin/base with no .exe
    suffix, so a filename test calls it POSIX and is wrong in the one case that
    matters. Four bytes off the front cannot be fooled that way.
    """
    if not path:
        return "absent"
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError:
        return "unreadable"
    if magic[:2] == b"MZ":
        return "pe"
    if magic == b"\x7fELF":
        return "elf"
    if magic[:4] == b"\xcf\xfa\xed\xfe" or magic[:4] == b"\xca\xfe\xba\xbe":
        return "macho"
    if magic[:2] == b"#!":
        return "script"
    return "unknown"


def operator_files() -> list[Path]:
    """Every operator file this run could contaminate, on BOTH platform tiers.

    ABSENT IS A PASSING VALUE. That is the whole reason this function exists
    instead of a hard-coded list. The previous version watched
    ``/mnt/c/Users/<home.name>/.base-gbl/base.toml``, and there is no username
    for which that resolves: the Windows account is `Chris` and the WSL account
    is `chriskahler`, so from WSL it pointed at a directory that does not exist
    and from Windows a POSIX prefix resolves nowhere either. It therefore hashed
    the constant string "absent" on every run -- stable before, stable after,
    and the isolation row passed while the file it was named for filled up with
    junk. A watched path that cannot resolve is not a watch, it is a no-op that
    reads green.

    So the cross-tier side is GLOBBED rather than constructed, and the caller
    refuses when the other platform's tier root exists but the glob finds
    nothing. THAT REFUSAL STAYS. Narrowing this set must not quietly drop the
    anti-no-op check that earned it.

    WHAT LEFT THIS SET, AND WHY IT IS NOT A LOSS OF COVERAGE. Three paths moved
    to `churning_files()` below: both graphs and the change feed. They are
    written by ANY live base session, not only by this run, so an exact hash
    over them cannot tell "this run contaminated the operator's knowledge" from
    "another session was working while the harness ran". They are still watched,
    by `fingerprint_hits()`, on a question a foreign session cannot answer for
    them. Everything remaining here is written by nothing but a leak, so an
    exact hash is still the right instrument for it and its row is unchanged.
    """
    home = Path.home()
    files = [home / ".base-gbl" / "base.toml",
             home / ".base-gbl" / ".base" / "domains.toml",
             home / ".claude" / "CLAUDE.md"]
    # Installed extensions: a hand-install overwrote one of these tonight and
    # left `base cadre` dead, so the directory is watched as a whole.
    files.extend(sorted((home / ".base-gbl" / "extensions").glob("*.toml")))
    files.extend(cross_tier_files())
    return files


def cross_tier_root() -> Path:
    """Where the OTHER platform's home directories live, seen from this one."""
    return (Path("//wsl.localhost/Ubuntu/home") if IS_WIN
            else Path("/mnt/c/Users"))


def cross_tier_files() -> list[Path]:
    return sorted(cross_tier_root().glob("*/.base-gbl/base.toml"))


def operator_digest(files: list[Path] | None = None) -> str:
    """One content hash over ``files``, defaulting to every operator file.

    Content hash and never mtime: `fs::copy` preserves mtime, so a harness
    keyed on it reports "unchanged" while writing. Keyed by full path, not by
    name, because several of these are called base.toml.

    ``files`` is injectable so the harness can prove the mechanism detects a
    change at all, rather than assuming a stable number means nothing moved.
    """
    parts = []
    for f in (operator_files() if files is None else files):
        try:
            parts.append(str(f) + ":" + hashlib.md5(f.read_bytes()).hexdigest())
        except OSError:
            parts.append(str(f) + ":absent")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def churning_files() -> list[Path]:
    """The operator files that ANY live base session writes, not just this run.

    MEASURED at 5d19e1b45f75 on a Windows host with 11 live relay sessions and
    this harness NOT running. Every path in `operator_files()` and every path
    here was polled every 5 seconds for 240 seconds, counting CONTENT changes
    (md5) separately from mtime touches:

        ~/.base-gbl/.base/graph.nq        13 content changes   +27,742 bytes
        ~/.base-gbl/.base/changes.jsonl   13 content changes   +23,564 bytes
        ~/.base/graph.nq                   9 content changes    +2,318 bytes
        the other nine watched paths       0 content changes        +0 bytes

    It is EVENT-driven, not clock-driven: a 10-second window inside one idle
    turn moved nothing at all, on either platform tier. Over the minutes a real
    run takes, with sessions live, it is hit every time.

    The failure that caused was a FALSE RED on the row that outranks every
    other row in this harness -- and a guard that fails on a correct run gets
    edited to match the code, which is how it stops guarding.
    """
    home = Path.home()
    return [home / ".base" / "graph.nq",
            home / ".base-gbl" / ".base" / "graph.nq",
            home / ".base-gbl" / ".base" / "changes.jsonl"]


def churn_baseline(files: list[Path] | None = None) -> dict[str, tuple]:
    """(size, md5 of the first 4 KiB) per churning file, taken before the run.

    The head hash is what tells an APPEND from a REWRITE. base rotates these
    files -- `graph.nq.bak-compact-*` sits beside them on disk -- and after a
    compaction a byte offset into the new file points at unrelated content.

    THE LENGTH OF THE HEAD IS STORED, not assumed to be 4096, and that is not
    a detail. A file SHORTER than 4 KiB has a head that is the whole file, so
    after an append `data[:4096]` is a different span of bytes than the one
    that was hashed and the comparison fails on every small file -- which sends
    every one of them down the compaction fallback, where only the run-unique
    marker is matched. The scan would have been quietly narrower than it says
    it is. Found by the test for it, not by reading.

    `files` is injectable so the arms can prove the mechanism on a copy inside
    the sandbox rather than on the operator's own file.
    """
    out: dict[str, tuple] = {}
    for f in (churning_files() if files is None else files):
        try:
            with open(f, "rb") as fh:
                head = fh.read(4096)
            out[str(f)] = (f.stat().st_size, len(head),
                           hashlib.md5(head).hexdigest())
        except OSError:
            out[str(f)] = (0, 0, "absent")
    return out


def fingerprint_hits(baseline: dict[str, tuple], run_marker: str,
                     shared_markers: tuple[str, ...],
                     files: list[Path] | None = None) -> list[str]:
    """Anything in the churning files that carries THIS run's fingerprint.

    Only the bytes appended past the recorded offset are read. Another session's
    triples cannot contain this run's `mkdtemp` sandbox path, so foreign churn
    is ignored BY CONSTRUCTION rather than by a threshold that would need
    tuning.

    THE FALLBACK, AND THE TRAP INSIDE IT. If the file shrank or its first 4 KiB
    changed, base compacted or rotated it and the offset means nothing, so the
    whole file is scanned instead. IN THAT BRANCH ONLY `run_marker` IS MATCHED,
    never `shared_markers`. The sandbox BASE_HOME is a FIXED path -- it is
    `<drive>/cadre-accept-base` on Windows and `<tmp>/cadre-accept-base`
    elsewhere, identical on every run on this host. Inside the append window
    those bytes are this run's by construction, so it is safe there. Over the
    whole file it is not: a PREVIOUS run's leftover `cadre-accept-base` triples
    would be read as today's contamination and the row would go red for
    something that happened last week. Only the mkdtemp sandbox path is unique
    to a run.
    """
    hits = []
    for f in (churning_files() if files is None else files):
        size0, head_len, head0 = baseline.get(str(f), (0, 0, "absent"))
        try:
            data = f.read_bytes()
        except OSError:
            continue
        if head0 == "absent":
            # It did not exist when the run started, so everything in it is
            # this run's era and every marker is safe.
            window, markers, how = data, (run_marker,) + shared_markers, "created"
        elif len(data) >= size0 and hashlib.md5(
                data[:head_len]).hexdigest() == head0:
            window, markers, how = (data[size0:],
                                    (run_marker,) + shared_markers, "appended")
        else:
            window, markers, how = data, (run_marker,), "whole file, compacted"
        blob = window.decode("utf-8", errors="replace")
        for m in markers:
            if m and m in blob:
                hits.append(f"{f} [{how}] carries {m}")
    return hits


def venv_bin(venv: Path, name: str) -> Path:
    d = venv / ("Scripts" if IS_WIN else "bin")
    return d / (name + (".exe" if IS_WIN else ""))


def _make_output_utf8_safe() -> None:
    """Never let this harness die on its own report.

    The harness echoes child stdout, and `cadre doctor` prints U+2713. On
    Windows a redirected or piped stdout is cp1252, which cannot encode that
    character, so printing it raises UnicodeEncodeError and the harness exits 1
    — indistinguishable, to a reader, from the product failing.

    Measured on a real Windows host with PYTHONIOENCODING cleared: redirected
    stdout reports cp1252; `print("\u2713")` exits 1 with an EMPTY output file;
    after this call it exits 0 and writes E2 9C 93.

    errors="replace" rather than "strict": a stray undecodable byte in some
    child's output must not take the run down either. A mangled glyph in a
    report is a cosmetic loss; a dead harness is a lost measurement.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # Older Python, or a stream that is not a TextIOWrapper (pytest
            # capture, a pipe wrapper). Degrading to whatever the platform
            # gives us is correct; refusing to run is not.
            pass


def main() -> int:
    _make_output_utf8_safe()
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="leave the sandbox on disk for inspection")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--wheel", help="use this wheel instead of building one")
    a = ap.parse_args()

    b = Board()
    print("=" * 78)
    print("CADRE END-TO-END ACCEPTANCE")
    print("=" * 78)
    print(f"  os          {platform.platform()}")
    print(f"  sys.platform{'':<1}{sys.platform}")
    print(f"  python      {sys.version.split()[0]}  ({sys.executable})")
    print(f"  repo        {REPO}")
    print(f"  PYTHONIOENCODING={os.environ.get('PYTHONIOENCODING') or '(unset)'}")
    if os.environ.get("PYTHONIOENCODING"):
        print("  NOTE: PYTHONIOENCODING is set in this environment. It MASKS "
              "locale-encoding defects. Clear it for an honest run.")
    print()

    # Opened HERE, before the first command, and closed in the last row.
    #
    # It used to be taken inside the base section, hundreds of lines below the
    # `cadre init` calls it needed to watch, so it could only ever see its own
    # section. Widening WHAT it hashed would not have helped: the window itself
    # was in the wrong place, and the contamination happened before it opened.
    # REFUSE rather than record a placeholder. If the other platform's tier
    # root is right there and the glob finds nothing under it, the cross-tier
    # arm is dead and every "unchanged" this run reports is worth nothing.
    if cross_tier_root().is_dir() and not cross_tier_files():
        print(f"  REFUSING: {cross_tier_root()} exists but no tier was found "
              "under it. The cross-platform isolation check would silently "
              "watch nothing, which is how six junk workspaces reached the "
              "operator's registry while this harness reported clean.")
        return 3

    run_start_digest = operator_digest()
    # Taken in the same breath as the digest and for the same reason: a window
    # that opens after the contamination measures nothing.
    run_start_churn = churn_baseline()
    print(f"  watching    {len(operator_files())} operator file(s) by exact "
          f"hash, {len(churning_files())} by append scan, "
          f"{len(cross_tier_files())} on the other tier")

    sandbox = Path(tempfile.mkdtemp(prefix="cadre-accept-"))

    # `cadre init` scaffolds the firm's BASE tier, so it shells out to whatever
    # base this host carries. Before that landed these calls were inert; now an
    # un-isolated one registers this throwaway sandbox in the OPERATOR's global
    # workspace registry, which `base workspace sync` then copies into the
    # CLAUDE.md they load in every session. Measured: six `cadre-accept-*`
    # entries from three runs, on both platforms.
    #
    # CADRE_NO_BASE is the load-bearing half. BASE_HOME alone does not hold on
    # WSL, where `base` resolves to the WINDOWS binary across /mnt/c and a POSIX
    # BASE_HOME neither redirects its write nor trips base's own isolation
    # panic. Not finding a binary at all is the only thing that reliably stops a
    # child process.
    SANDBOX_ENV = {"CADRE_NO_BASE": "1",
                   "BASE_HOME": str(sandbox / "base-home")}
    venv = sandbox / "venv"
    ws = sandbox / "demo-firm"
    print(f"  sandbox     {sandbox}")
    print()

    # Success path only. The finally: below writes the summary whatever
    # happens, which is what makes the summary trustworthy about a run that
    # finished and untrustworthy about one that did not. This is the bit that
    # tells the two apart.
    finished = False

    def end_run(reason: str | None = None) -> int:
        """The ONE way out of this harness, section or no section.

        Does the four things a bare `return b.report()` did not: fills every
        declared row this path never reached, checks the whole scoreboard
        against the declared list, marks the run finished so the JSON summary
        stops describing it as a run that died, and only then reports.

        A note for whoever reads a double failure here: if the base section's
        own conservation check has already added a FAIL row, that row is not in
        HARNESS_ROWS, so the check below adds a second one. Two FAIL rows, both
        true. The base one names which base row moved; this one names the
        extra. Neither is noise.
        """
        nonlocal finished
        if reason:
            b.skip_rest(HARNESS_ROWS, reason)
        b.assert_section(0, HARNESS_ROWS, "whole harness")
        finished = True
        return b.report()

    try:
        # ---- 1. wheel ---------------------------------------------------
        # Two arms, two DIFFERENT row names, so whichever one runs the other
        # name would simply be absent -- and an absent row says nothing at all.
        # Each arm records the sibling it did not take.
        if a.wheel:
            wheel = Path(a.wheel)
            b.add(PASS if wheel.is_file() else FAIL, "wheel provided",
                  str(wheel))
            b.skip_rest(["build a wheel from source"],
                        "not measured: a wheel was supplied with --wheel, so "
                        "this run never built one. Unmeasured, not passed.")
        else:
            b.skip_rest(["wheel provided"],
                        "not measured: no --wheel was given, so this run built "
                        "its own. Unmeasured, not passed.")
            dist = sandbox / "dist"
            rc, out = run([sys.executable, "-m", "pip", "install", "--quiet",
                           "--upgrade", "build"])
            rc, out = run([sys.executable, "-m", "build", "--outdir", str(dist)],
                          cwd=REPO)
            wheels = sorted(dist.glob("*.whl"))
            b.add(PASS if (rc == 0 and wheels) else FAIL,
                  "build a wheel from source",
                  f"rc={rc}\n" + ("\n".join(w.name for w in wheels)
                                  or out[-500:]))
            if not wheels:
                return end_run(
                    "not measured: no wheel was produced, so there was nothing "
                    "to install and nothing below could run. Unmeasured, not "
                    "passed.")
            wheel = wheels[0]

        # ---- 2. clean venv + install ------------------------------------
        rc, out = run([sys.executable, "-m", "venv", str(venv)])
        b.add(PASS if rc == 0 else FAIL, "create an empty virtual environment",
              f"rc={rc}\n{out[-300:]}")
        vpy = venv_bin(venv, "python")
        rc, out = run([str(vpy), "-m", "pip", "install", "--quiet",
                       "--upgrade", "pip"])
        rc, out = run([str(vpy), "-m", "pip", "install", str(wheel)])
        b.add(PASS if rc == 0 else FAIL, "pip install the wheel into it",
              f"rc={rc}\n{out[-600:]}")
        if rc != 0:
            return end_run(
                "not measured: the wheel did not install, so every row below "
                "is about a package that is not there. Unmeasured, not passed.")

        rc, freeze = run([str(vpy), "-m", "pip", "freeze"])
        mcp_line = next((l for l in freeze.splitlines()
                         if l.lower().startswith("mcp==")), "(mcp absent)")
        b.add(PASS if mcp_line.startswith("mcp==") else FAIL,
              "the mcp dependency resolved under its upper bound",
              mcp_line + "\n(an unbounded mcp resolved to a major version that "
                         "removed a module Cadre imports — issue #3)")

        # ---- 3. console scripts ----------------------------------------
        for exe in CONSOLE_SCRIPTS:
            rc, out = run([str(venv_bin(venv, exe)), "--version"])
            b.add(PASS if rc == 0 else FAIL, console_script_row(exe),
                  f"rc={rc}  {out.strip()[:120]}")

        # ---- 4. imports, with a CONTROL that they come from the venv ----
        probe = (
            "import importlib, json, sys\n"
            "names=['firm','firm.mcp.tools','firm.pulse.spawn',"
            "'firm.dashboard.server','firm.services.base_domain']\n"
            "bad=[]\n"
            "for n in names:\n"
            "    try: importlib.import_module(n)\n"
            "    except Exception as e: bad.append(n+': '+repr(e))\n"
            "import firm\n"
            "print(json.dumps({'bad':bad,'file':firm.__file__}))\n"
        )
        rc, out = run([str(vpy), "-c", probe], cwd=sandbox)
        try:
            got = json.loads(out.strip().splitlines()[-1])
        except Exception:
            got = {"bad": ["probe produced no JSON: " + out[-300:]], "file": ""}
        b.add(PASS if (rc == 0 and not got["bad"]) else FAIL,
              "every module imports from the installed package",
              "\n".join(got["bad"]) or "all five imported")
        # CONTROL: without this, the whole run could be exercising the repo.
        from_venv = str(venv).lower() in got.get("file", "").lower()
        b.add(PASS if from_venv else FAIL,
              "CONTROL: firm resolves from the venv, not from the source tree",
              f"firm.__file__ = {got.get('file')}\n"
              + ("" if from_venv else
                 "the harness is testing the SOURCE TREE; every result above "
                 "is about the repo, not about what a user installs"))

        rc, out = run([str(vpy), "-c",
                       "from firm.mcp.tools import mcp;"
                       "print(len(mcp._tool_manager._tools))"], cwd=sandbox)
        b.add(PASS if rc == 0 and out.strip().isdigit() else FAIL,
              "the MCP tool surface loads from the installed package",
              f"rc={rc}  tools={out.strip()[:40]}")

        # ---- 5. package DATA, separate from code -----------------------
        data_probe = (
            "import pathlib, firm, json\n"
            "r=pathlib.Path(firm.__file__).parent\n"
            "print(json.dumps({k:len(sorted(r.glob(g))) for k,g in "
            "[('migrations','migrations/*.sql'),"
            "('dashboard','dashboard/*.html'),"
            "('templates','templates/**/*.md')]}))\n"
        )
        rc, out = run([str(vpy), "-c", data_probe], cwd=sandbox)
        try:
            counts = json.loads(out.strip().splitlines()[-1])
        except Exception:
            counts = {}
        missing = [k for k, v in counts.items() if not v]
        b.add(PASS if counts and not missing else FAIL,
              "the wheel carries its data files, not only its code",
              json.dumps(counts) + ("" if not missing else
                                    f"\nmissing: {missing} — a wheel can import "
                                    "perfectly and still ship no migrations"))

        # ---- 6. cadre init, with a CONTROL that the db was absent -------
        ws.mkdir(parents=True, exist_ok=True)
        db = ws / ".firm" / "firm.db"
        b.add(PASS if not db.exists() else FAIL,
              "CONTROL: no firm database exists before init",
              f"{db} absent" if not db.exists() else
              "a database already exists, so 'init created it' proves nothing")
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws), "--demo"],
                      env=SANDBOX_ENV)
        b.add(PASS if rc == 0 and db.is_file() else FAIL,
              "cadre init --demo creates a firm",
              f"rc={rc}\n{out[-700:]}")

        if db.is_file():
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            # Count applied migrations off sqlite_master rather than a
            # guessed table name: an earlier version queried
            # schema_migrations, which does not exist, and reported
            # "migrations=None" as if that were a finding.
            try:
                mig = c.execute(
                    "select count(*) from sqlite_master where type='table'"
                ).fetchone()[0]
            except sqlite3.Error:
                mig = None
            try:
                mem = c.execute("select count(*) from member").fetchone()[0]
                uni = c.execute("select count(*) from unit").fetchone()[0]
            except sqlite3.Error:
                mem = uni = 0
            c.close()
            b.add(PASS if mem > 0 else FAIL,
                  "the demo firm actually has a roster and work",
                  f"members={mem} units={uni} migrations={mig}")
        else:
            b.skip_rest(["the demo firm actually has a roster and work"],
                        f"not measured: no firm database at {db}, so there was "
                        "no roster to count. The row above says whether init "
                        "reported success. Unmeasured, not passed.")

        # ---- 7. hooks + idempotence ------------------------------------
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws),
                       "--install-hooks"], env=SANDBOX_ENV)
        hook = ws / ".claude" / "hooks" / "cadre-session-pulse.py"
        settings = ws / ".claude" / "settings.json"
        wired = settings.is_file() and "cadre-session-pulse" in \
            settings.read_text(encoding="utf-8", errors="replace")
        b.add(PASS if rc == 0 and hook.is_file() and wired else FAIL,
              "cadre init --install-hooks wires the session hook",
              f"rc={rc}  hook={hook.is_file()}  registered={wired}")

        # Registered is NOT running. The two checks above both pass on a hook
        # that can never execute, and on 2026-09-10 the shipped hook could not:
        # install_hooks.py registers it under bare `python3`, which is not the
        # interpreter that has `firm`. It exits 0, prints nothing, and its
        # contract says silence means "no firm here" — so the roster never
        # reaches a session and nothing reports it.
        #
        # This runs the command AS REGISTERED, taken out of settings.json
        # rather than chosen here. An interpreter of the harness's own picking
        # would prove the hook script works while saying nothing about what
        # `cadre init` actually wired, which is the whole defect.
        registered = _registered_session_hook_command(settings)
        if not registered:
            b.add(FAIL, "the session hook command can be read back out of "
                        "settings.json",
                  "no SessionStart command found; cannot test what was wired")
            b.skip_rest(
                ["the session hook AS REGISTERED actually emits the roster",
                 "RED ARM: the recorded interpreter path exists to be removed",
                 "RED ARM: with the recorded path removed the hook goes quiet "
                 "and says why",
                 "the registered hook emits the roster without a developer "
                 "editable install helping it"],
                "not measured: no SessionStart command could be read back, so "
                "there was no registered command to run. Unmeasured, not "
                "passed.")
        else:
            # This row only ever existed on the FAILURE arm, so a healthy run
            # left it off the scoreboard entirely and the run that could not
            # read the command produced a LONGER report than the run that
            # could. Recorded on both arms now.
            b.add(PASS, "the session hook command can be read back out of "
                        "settings.json", registered)
            payload = json.dumps({"cwd": str(ws)})
            argv = _expand_hook_command(registered, ws)
            rc_h, out_h = run(argv, cwd=ws, stdin_text=payload)
            got_roster = "active-roster" in out_h or "member" in out_h.lower()
            b.add(PASS if got_roster else FAIL,
                  "the session hook AS REGISTERED actually emits the roster",
                  f"command: {registered}\nargv: {argv}\nrc={rc_h} "
                  f"stdout={len(out_h)} bytes\n"
                  + (out_h[:300] if got_roster else
                     "EMPTY OR NO ROSTER. The hook exits 0 and says nothing, "
                     "which its contract reads as 'no firm here'. A session "
                     "gets no roster and nothing reports it. Check that the "
                     "registered interpreter is the one that has `firm`."))

            # RED ARM. Without one, a green row above could mean the harness
            # cannot tell a working hook from a broken one.
            #
            # This arm used to run the hook under `-s` and require no roster,
            # on the reasoning that an interpreter without cadre cannot serve
            # one. That stopped being true when the interpreter fix landed: the
            # hook now reads the path recorded in `.firm/python-path`, puts it
            # on sys.path itself, and serves the roster whichever interpreter
            # Claude Code invoked. The old arm went FAIL on main while the row
            # above went PASS -- the harness calling a fix a defect.
            #
            # So the discriminator is the recorded path, and the two arms now
            # differ by exactly one thing. Remove the marker and a firm that IS
            # here must go silent on stdout and LOUD on stderr, which is the
            # third state the fix introduced. User site stays suppressed in
            # both arms: without that, bare python3 on a developer box reaches
            # `firm` through an editable-install .pth no matter what the marker
            # says, and this arm would pass for the wrong reason.
            marker = ws / ".firm" / "python-path"
            saved = marker.read_bytes() if marker.is_file() else None
            if saved is None:
                b.add(FAIL, "RED ARM: the recorded interpreter path exists to "
                            "be removed",
                      f"{marker} is not a file, so the arm below would prove "
                      "nothing. cadre init --install-hooks is supposed to "
                      "record it.")
                b.skip_rest(
                    ["RED ARM: with the recorded path removed the hook goes "
                     "quiet and says why"],
                    "not measured: there was no recorded path to remove, so "
                    "the arm had nothing to do. Unmeasured, not passed.")
            else:
                # Same shape as the row above: this one was recorded only when
                # it FAILED, so a healthy run never put it on the board.
                b.add(PASS, "RED ARM: the recorded interpreter path exists to "
                            "be removed", f"{marker} ({len(saved)} bytes)")
                try:
                    marker.unlink()
                    rc_b, out_b = run(argv, cwd=ws, stdin_text=payload,
                                      env={"PYTHONNOUSERSITE": "1",
                                           "FIRM_SRC": ""})
                finally:
                    marker.write_bytes(saved)
                quiet = "active-roster" not in out_b
                loud = "could not be imported" in out_b and "paths tried" in out_b
                b.add(PASS if quiet and loud else FAIL,
                      "RED ARM: with the recorded path removed the hook goes "
                      "quiet and says why",
                      f"rc={rc_b} output={len(out_b)} bytes\n"
                      + ("correctly emitted no roster and named the "
                         "interpreter and the paths it tried"
                         if quiet and loud else
                         ("it STILL emitted a roster, so the row above cannot "
                          "distinguish a working hook from a broken one"
                          if not quiet else
                          "it emitted no roster but said nothing about why. "
                          "That is the silent-success failure the fix exists "
                          "to remove: an operator sees the same nothing as an "
                          "empty directory.")))

            # Green above is still not proof a USER gets a roster. On a
            # developer box bare `python3` reaches `firm` through an
            # editable-install .pth in USER site-packages, and it resolves to
            # the SOURCE TREE, not the installed wheel. Measured 2026-09-10:
            # bare python3 imported firm from src/, while both `-s` and
            # PYTHONNOUSERSITE=1 raised ModuleNotFoundError. Suppressing user
            # site-packages removes the accident and leaves what a fresh user
            # actually has, so this arm separates "works here" from "works".
            rc_u, out_u = run(argv, cwd=ws, stdin_text=payload,
                              env={"PYTHONNOUSERSITE": "1"})
            got_u = "active-roster" in out_u or "member" in out_u.lower()
            b.add(PASS if got_u else FAIL,
                  "the registered hook emits the roster without a developer "
                  "editable install helping it",
                  f"command: {registered}\nrc={rc_u} "
                  f"stdout={len(out_u)} bytes\n"
                  + (out_u[:300] if got_u else
                     "NO ROSTER once user site-packages is suppressed. The row "
                     "above is green only because THIS machine carries an "
                     "editable-install .pth that a user does not have. The "
                     "registered interpreter cannot import cadre for anyone "
                     "else, so the hook exits 0 and prints nothing, and its "
                     "contract reads that silence as 'no firm here'. Record "
                     "the interpreter path at install time and have the hook "
                     "read it."))
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws), "--demo"],
                      env=SANDBOX_ENV)
        again_ok = rc == 0 and ("skip" in out.lower() or "already" in out.lower())
        b.add(PASS if again_ok else FAIL,
              "re-running init is idempotent, not destructive",
              f"rc={rc}\n{out[-400:]}")

        # ---- 8. the CLI journey a Member and the Board actually drive ---
        firm_exe = str(venv_bin(venv, "firm"))
        member = None
        if db.is_file():
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                member = c.execute("select id from member limit 1").fetchone()[0]
                unit = c.execute("select id from unit limit 1").fetchone()[0]
            except sqlite3.Error:
                unit = None
            c.close()

        if not member:
            b.add(FAIL, "a member exists to act as", "no member row to drive")
            b.skip_rest(
                ["a Member can ask the Board for approval (firm gate request)",
                 "a Member can escalate to the Board (firm escalation raise)"],
                "not measured: there was no member to act as, so neither the "
                "gate nor the escalation path could be driven. Unmeasured, not "
                "passed.")
        else:
            # Third one of these. Recorded only on the failure arm, so the
            # broken run reported it and the healthy run did not.
            b.add(PASS, "a member exists to act as", f"member={member}")
            env = {"CADRE_MEMBER_ID": member}
            rc, out = run([firm_exe, "gate", "request",
                           "--action", "acceptance harness: prove the gate path",
                           "--target-type", "unit", "--target-id", str(unit),
                           "--context", "written by scripts/acceptance-e2e.py"],
                          cwd=ws, env=env)
            # Read the row BACK. rc 0 alone does not prove a row landed.
            gate_row = None
            if db.is_file():
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                c.row_factory = sqlite3.Row
                try:
                    r = c.execute("select * from gate order by rowid desc "
                                  "limit 1").fetchone()
                    gate_row = dict(r) if r else None
                except sqlite3.Error:
                    pass
                c.close()
            ok = (rc == 0 and gate_row
                  and gate_row.get("requesting_member_id") == member
                  and gate_row.get("status") == "pending")
            b.add(PASS if ok else FAIL,
                  "a Member can ask the Board for approval (firm gate request)",
                  f"rc={rc}\n"
                  + (f"gate={gate_row.get('id')} status={gate_row.get('status')} "
                     f"by={gate_row.get('requesting_member_id')}"
                     if gate_row else "NO GATE ROW LANDED\n" + out[-300:]))

            rc, out = run([firm_exe, "escalation", "raise",
                           "--title", "acceptance harness: prove the escalation path"],
                          cwd=ws, env=env)
            esc_ok = rc == 0
            if db.is_file() and esc_ok:
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    esc_ok = c.execute(
                        "select count(*) from escalation where title like "
                        "'acceptance harness%'").fetchone()[0] > 0
                except sqlite3.Error:
                    esc_ok = False
                c.close()
            b.add(PASS if esc_ok else FAIL,
                  "a Member can escalate to the Board (firm escalation raise)",
                  f"rc={rc}\n{out[-300:] if not esc_ok else 'row read back'}")

        # ---- 9. the host scheduler for THIS OS -------------------------
        sched_probe = (
            "import json\n"
            "from firm.sched import resolve_scheduler\n"
            "s=resolve_scheduler(); ok,d=s.available()\n"
            "print(json.dumps({'backend':type(s).__name__,'ok':bool(ok),"
            "'detail':str(d)[:200]}))\n"
        )
        rc, out = run([str(vpy), "-c", sched_probe], cwd=sandbox)
        try:
            sc = json.loads(out.strip().splitlines()[-1])
        except Exception:
            sc = {"backend": "?", "ok": False, "detail": out[-200:]}
        b.add(PASS if sc["ok"] else FAIL,
              "the host scheduler for this OS resolves and is available",
              f"backend={sc['backend']} available={sc['ok']} {sc['detail']}")

        # ---- 10. spawn: the step that has never run off Linux ----------
        # Negative arm FIRST. If a bogus binary "spawns fine", the positive
        # arm below proves nothing at all.
        bogus = str(sandbox / "definitely-not-here")
        spawn_probe = (
            "import json,sys\n"
            "from firm.pulse.spawn import resolve_claude_bin\n"
            "p,d=resolve_claude_bin()\n"
            "print(json.dumps({'path':p,'detail':d[:200]}))\n"
        )
        rc, out = run([str(vpy), "-c", spawn_probe], cwd=sandbox,
                      env={"CADRE_CLAUDE_BIN": bogus})
        try:
            neg = json.loads(out.strip().splitlines()[-1])
        except Exception:
            neg = {"path": "?", "detail": out[-200:]}
        b.add(PASS if neg["path"] is None else FAIL,
              "NEGATIVE CONTROL: a nonexistent runtime is refused",
              f"resolved={neg['path']}\n{neg['detail']}")

        native = (os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe") \
            if IS_WIN else "/bin/echo"
        rc, out = run([str(vpy), "-c", spawn_probe], cwd=sandbox,
                      env={"CADRE_CLAUDE_BIN": native})
        try:
            pos = json.loads(out.strip().splitlines()[-1])
        except Exception:
            pos = {"path": None, "detail": out[-200:]}
        if pos["path"]:
            b.add(PASS, "a native executable is accepted as the Member runtime",
                  f"{native} -> accepted")
        else:
            b.add(BLOCKED,
                  "a native executable is accepted as the Member runtime",
                  f"{native} REFUSED: {pos['detail']}\n"
                  "KNOWN DEFECT: _is_execable in firm/pulse/spawn.py accepts "
                  "only ELF (\\x7fELF) or a shebang, so PE (Windows) and Mach-O "
                  "(macOS) binaries are rejected and no Member can spawn. "
                  "Nothing below this line can run until it is fixed.")

        # ---- 11. base as the engine, via the extension -----------------
        #
        # EVERY PATH OUT OF THIS SECTION RECORDS ALL EIGHTEEN OF ITS ROWS.
        # It used to record one SKIP and drop the other seventeen on a host
        # whose `base` is built for another platform, so the same harness wrote
        # a full scoreboard on one machine and a much shorter one on another --
        # and nothing in the shorter one said the difference was unmeasured
        # rows rather than a smaller job. A row that is absent says nothing at
        # all; a SKIP row says what this host could not check and why.
        #
        # BASE_SECTION_ROWS is the declared list, `skip_rest` fills whatever an
        # early exit did not reach, and `end_base_section` asserts the two
        # against each other on the way out.
        base_section_start = len(b.rows)
        base_home: Path | None = None

        def end_base_section() -> int:
            """The ONE way out of this section.

            A bare `return b.report()` did none of these four things: it left
            the remaining rows unrecorded, skipped the conservation check, left
            the sandbox BASE_HOME on disk, and -- since the `completed` flag
            landed -- returned without setting `finished`, so the summary said
            the harness had died when it had declined to measure on purpose.
            """
            b.assert_section(base_section_start, BASE_SECTION_ROWS,
                             "base as the engine")
            if base_home is not None:
                shutil.rmtree(base_home, ignore_errors=True)
            # Routed through the one exit rather than reporting directly, so
            # the WHOLE scoreboard is conserved and not only this section's
            # part of it. `end_run` sets `finished`.
            return end_run()

        rc, out = run(["base", "--version"])
        base_path = shutil.which("base")
        base_kind = binary_kind(base_path)
        native_kind = "pe" if IS_WIN else ("macho" if sys.platform == "darwin" else "elf")
        if rc != 0:
            b.add(SKIP, "base is installed on this host",
                  "base not on PATH; the extension journey cannot be checked "
                  "here. This is a host setup fact, not a Cadre defect.")
            b.skip_rest(BASE_SECTION_ROWS,
                        "not measured: base is not on PATH on this host, so "
                        "nothing in the base-engine section could run. "
                        "Unmeasured, not passed.")
        elif base_kind not in (native_kind, "script", "unknown"):
            # A base built for the OTHER platform still RUNS here -- WSL interop
            # executes the Windows binary quite happily -- and that is what makes
            # it dangerous rather than merely broken. It does not understand a
            # POSIX BASE_HOME, so every isolated call below would land in the
            # operator's REAL tier while each assertion passed. Measured tonight:
            # six junk workspaces in the operator's global registry, written by
            # runs that looked clean.
            #
            # Skipped rather than failed, and rather than aborted, for the same
            # reason the arm above skips a host with no base: which binary this
            # shell resolves is a host setup fact, not a Cadre defect. A section
            # that says THIS HOST CANNOT CHECK THIS is honest. One that runs
            # anyway writes somebody's knowledge.
            # `base` IS installed here -- that row is a PASS. Only the
            # platform match fails, and the difference between "this host has
            # no base" and "this host has the wrong one" is worth keeping.
            b.add(PASS, "base is installed on this host", out.strip()[:80])
            b.add(SKIP, "the resolved base matches this platform",
                  f"`base` resolves to {base_path} — a {base_kind} binary under "
                  f"a {native_kind} interpreter. A cross-platform base ignores "
                  "BASE_HOME, so this section would write the operator's own "
                  "tier instead of the sandbox. Put a native base first on PATH "
                  "to check this section here.")
            b.skip_rest(BASE_SECTION_ROWS,
                        "not measured: the resolved `base` is built for another "
                        "platform, so running this section would write the "
                        "operator's own tier instead of the sandbox. "
                        "Unmeasured, not passed.")
        else:
            b.add(PASS, "base is installed on this host", out.strip()[:80])
            b.add(PASS, "the resolved base matches this platform",
                  f"{base_path} ({base_kind})")
            rc2, out2 = run([str(vpy), "-c",
                             "import firm.services.base_domain as m;"
                             "print(getattr(m,'__file__',''))"], cwd=sandbox)
            b.add(PASS if rc2 == 0 else FAIL,
                  "the base bridge module loads from the installed package",
                  out2.strip()[:160])
            # ---- base is the engine: one row per assertion ----------
            #
            # Not one "base is the engine" row. Eleven, because when this goes
            # red the operator needs the step that broke, not the news that
            # something in a ten-command journey did. Each has its control
            # beside it: the negative arm that fails if the check has gone
            # blind. Measured end to end on Windows first; the sequence is
            # .base-gbl/docs/2026-09-10-godwit-base-engine-journey.md.
            #
            # TWO TRAPS PAID FOR ALREADY, both live in these four lines.
            #
            # `sandbox` is tempfile.mkdtemp(), which on Windows lands under
            # AppData\Local\Temp — INSIDE the user profile. base's own write
            # tripwire panics there, because `dirs` never consults $HOME on
            # Windows and cannot tell a fake tier under the profile from the
            # real one. So this section takes a drive-root BASE_HOME instead.
            #
            # And BASE_HOME is only half of it: it governs the GLOBAL tier.
            # The WORKSPACE tier follows the CURRENT DIRECTORY. Set BASE_HOME,
            # run from the wrong cwd, and base reads and writes the operator's
            # own graph while every assertion here still passes. Every base
            # call below therefore passes cwd=ws.
            base_home = (Path(Path(sys.executable).drive + "/cadre-accept-base")
                         if IS_WIN else Path(tempfile.gettempdir()) / "cadre-accept-base")
            shutil.rmtree(base_home, ignore_errors=True)
            base_home.mkdir(parents=True, exist_ok=True)
            benv = {"BASE_HOME": str(base_home), "PYTHONIOENCODING": "utf-8"}

            def bmember(mid: str | None) -> dict:
                e = dict(benv)
                e["CADRE_MEMBER_ID"] = mid or ""
                return e

            before_hash = run_start_digest
            churn_before = run_start_churn

            # 1. Isolation. Nothing else in this section may run until base is
            #    demonstrably reading the FIRM's tier, because if it is reading
            #    the operator's, every row below is writing their knowledge.
            rc, out = run(["base", "scaffold", "."], cwd=ws, env=benv)
            rc, out = run(["base", "doctor"], cwd=ws, env=benv)
            reads_firm = str(ws) in out.replace("/", os.sep)
            b.add(PASS if reads_firm else FAIL,
                  "base reads the firm's own workspace tier, not the operator's",
                  (f"tier under {ws}" if reads_firm else
                   "base did NOT name the firm's directory. BASE_HOME governs "
                   "the global tier only; the workspace tier follows cwd. "
                   "Refusing to run the rest of this section."))
            if not reads_firm:
                b.skip_rest(BASE_SECTION_ROWS,
                            "not measured: base was reading a tier other than "
                            "the firm's, so every row below would have been "
                            "writing the operator's own knowledge.")
                return end_base_section()

            # 2. The extension, through the shipped verb rather than a
            #    python -c. `read_back` is the claim that matters: install
            #    re-reads the landed file instead of trusting its own write,
            #    so "ok" alone would pass over a manifest that never landed.
            rc, out = run([str(venv_bin(venv, "cadre")), "extension", "install"],
                          cwd=ws, env=benv)
            landed = base_home / ".base-gbl" / "extensions" / "cadre.toml"
            wired = rc == 0 and "read back" in out and landed.exists()
            b.add(PASS if wired else FAIL,
                  "the Cadre manifest installs into base and is read back",
                  out.strip()[:160] if wired else
                  f"rc={rc} landed={landed.exists()} :: {out.strip()[:200]}")

            # 2b. THE ROW ABOVE CANNOT FAIL IN THE WAY THAT MATTERS, which is
            #     the whole reason this one exists. `rc == 0`, the words "read
            #     back" in the output, and a file on disk were ALL THREE TRUE
            #     at d842529 while `base cadre` exited 127 on both platforms.
            #     Measured, not reasoned about. An install reporting success
            #     over a dead command IS F1, and nothing short of running the
            #     handler can tell the two apart.
            #
            #     Note what this invokes: `base cadre`, the extension handler.
            #     Every other cadre call in this section goes through the venv
            #     console script, which reaches the CLI without ever testing
            #     the manifest that is supposed to point at it.
            rc, out = run(["base", "cadre", "--version"], cwd=ws, env=benv)
            runs = rc == 0 and "cadre" in out.lower()
            b.add(PASS if runs else FAIL,
                  "the extension's command RUNS through base, not merely installs",
                  f"`base cadre --version` -> {out.strip()[:120]}" if runs else
                  f"rc={rc} :: {out.strip()[:200]} :: the manifest is installed "
                  "and the command is dead — this is the F1 shape")

            # 3. A generated domain block is not a live wire. base drops a
            #    MATCHED domain carrying zero rules, silently and totally, so
            #    the block existing proves nothing. The rule count is the
            #    assertion; the block is not.
            rc, out = run([str(vpy), "-c",
                           "import sqlite3;"
                           "from pathlib import Path;"
                           "from firm.services import base_domain;"
                           "c = sqlite3.connect('.firm/firm.db');"
                           "c.row_factory = sqlite3.Row;"
                           "r = base_domain.sync(Path('.').resolve(), "
                           "__import__('firm.core.db', fromlist=['x'])"
                           ".resolve_firm_id(c, None), conn=c);"
                           "c.close(); print(r)"], cwd=ws, env=benv)
            firm_id = None
            try:
                with sqlite3.connect(f"file:{ws / '.firm' / 'firm.db'}?mode=ro",
                                     uri=True) as c:
                    firm_id = c.execute("select id from firm limit 1").fetchone()[0]
            except sqlite3.Error:
                pass
            rc2, out2 = run(["base", "rule", "list", "--domain", str(firm_id)],
                            cwd=ws, env=benv)
            live_rule = " 0 rules" not in out2 and "rules" in out2
            b.add(PASS if live_rule else FAIL,
                  "the firm's base domain carries a live rule",
                  out2.strip().splitlines()[0][:140] if out2.strip() else
                  "no rule count came back; base drops a domain with zero rules "
                  "whole, so the block can be perfect and inject nothing")

            # 4/5. Per-member scope, and the control that makes "different"
            #      mean something. Two Members must see different briefings AND
            #      a Board session must see none — without the second, two
            #      arbitrary strings would pass.
            members: list[str] = []
            try:
                with sqlite3.connect(f"file:{ws / '.firm' / 'firm.db'}?mode=ro",
                                     uri=True) as c:
                    members = [r[0] for r in
                               c.execute("select id from member order by id")]
            except sqlite3.Error:
                pass
            if len(members) < 2:
                # Named exactly as BASE_SECTION_ROWS names it. This row used to
                # read "two Members get different briefings" on this path and
                # "two Members of one firm get different briefings" on the
                # other, so one assertion wore two names depending on the host
                # and no reader could line the two scoreboards up.
                b.add(SKIP, "the demo firm has a Unit to close",
                      f"the demo firm has {len(members)} member(s); the unit "
                      "rows need two and are not asserting anything")
                b.add(SKIP, "two Members of one firm get different briefings",
                      f"the demo firm has {len(members)} member(s); this row "
                      "needs two and is not asserting anything")
                b.skip_rest(BASE_SECTION_ROWS,
                            f"not measured: the demo firm has {len(members)} "
                            "member(s) and these rows need two.")
            else:
                one, two = members[0], members[1]
                # The demo firm's unit ids are not this harness's to assume.
                # Read the first one, give it to Member one, and mint a second
                # for Member two so the two briefings have different content
                # to differ ABOUT — two empty briefings are also "different".
                _, uout = run([str(vpy), "-c",
                               "import sqlite3, sys;"
                               "c = sqlite3.connect('.firm/firm.db');"
                               "u = [r[0] for r in c.execute("
                               "'select id from unit order by id')];"
                               "c.execute('update unit set assignee_member_id=?"
                               " where id=?', (sys.argv[1], u[0]));"
                               "c.execute(\"insert into unit (id, firm_id, name,"
                               " project_id, assignee_member_id, status) select"
                               " 'ACC-U2', firm_id, 'second unit', project_id,"
                               " ?, 'in_progress' from unit where id=?\","
                               " (sys.argv[2], u[0]));"
                               "c.commit(); c.close(); print(u[0])",
                               one, two], cwd=ws, env=benv)
                unit_one = (uout.strip().splitlines() or [""])[-1].strip()
                if not unit_one:
                    b.add(FAIL, "the demo firm has a Unit to close",
                          "no unit id came back, so the gate rows below would "
                          "have asserted nothing")
                    b.skip_rest(BASE_SECTION_ROWS,
                                "not measured: there was no Unit to close, so "
                                "the gate and write-back rows had nothing to "
                                "act on.")
                    return end_base_section()
                b.add(PASS, "the demo firm has a Unit to close", unit_one)
                _, b1 = run([str(venv_bin(venv, "cadre")), "brief"],
                            cwd=ws, env=bmember(one))
                _, b2 = run([str(venv_bin(venv, "cadre")), "brief"],
                            cwd=ws, env=bmember(two))
                differ = bool(b1.strip()) and bool(b2.strip()) and b1 != b2
                b.add(PASS if differ else FAIL,
                      "two Members of one firm get different briefings",
                      f"{one}: {b1.strip().splitlines()[0][:60] if b1.strip() else '(empty)'} | "
                      f"{two}: {b2.strip().splitlines()[0][:60] if b2.strip() else '(empty)'}")

                _, b0 = run([str(venv_bin(venv, "cadre")), "brief"],
                            cwd=ws, env=bmember(None))
                b.add(PASS if not b0.strip() else FAIL,
                      "a Board session is briefed as nobody (scope control)",
                      "silent, which is correct — the Board is not a Member"
                      if not b0.strip() else
                      "a Board session was handed a Member's queue: "
                      + b0.strip()[:120])

                # 6-8. The gate: armed, biting, and not biting the wrong person.
                run([str(vpy), "-c",
                     "from pathlib import Path;"
                     "from firm.cli.install_hooks import install_writeback_hook;"
                     "install_writeback_hook(Path('.').resolve())"],
                    cwd=ws, env=benv)
                gate = ws / ".claude" / "hooks" / "cadre-writeback-gate.py"

                def fire_gate(mid: str) -> int:
                    payload = json.dumps({"cwd": str(ws), "stop_hook_active": False})
                    p = subprocess.run([str(vpy), str(gate)], cwd=str(ws),
                                       env={**os.environ, **bmember(mid)},
                                       input=payload.encode(),
                                       capture_output=True, timeout=120)
                    return p.returncode

                b.add(PASS if fire_gate(one) == 0 else FAIL,
                      "the write-back gate lets a Member with nothing owed finish",
                      "exit 0 with no debt")

                run([str(venv_bin(venv, "cadre")), "member", "grant",
                     "authority", one, "--comment", "acceptance"],
                    cwd=ws, env=benv)
                rc, out = run([str(venv_bin(venv, "cadre")), "complete", unit_one],
                              cwd=ws, env=bmember(one))
                blocked = fire_gate(one)
                b.add(PASS if blocked == 2 else FAIL,
                      "closing a Unit with nothing recorded blocks the session",
                      f"gate exit {blocked} (want 2) after: {out.strip()[:120]}")

                b.add(PASS if fire_gate(two) == 0 else FAIL,
                      "the other Member is not blocked by that debt (gate control)",
                      "exit 0 — a gate that blocks everybody passes the row "
                      "above and makes the framework unusable")

                # 9/10. The graph, read empty BEFORE so a note found AFTER is
                #       known to be this run's. Without the before-arm, a note
                #       from any earlier run proves nothing.
                _, empty = run(["base", "learn", "--list", "--domain",
                                str(firm_id)], cwd=ws, env=benv)
                was_empty = "No notes" in empty
                b.add(PASS if was_empty else FAIL,
                      "the firm's graph starts empty (evidence control)",
                      "no notes yet" if was_empty else
                      "the graph was NOT empty first, so a lesson found after "
                      "the write-back proves nothing about who put it there")

                lesson = ("The acceptance run taught that the gate and the "
                          "command are one piece.")
                rc, out = run([str(venv_bin(venv, "cadre")), "learn", "--unit",
                               unit_one, "--type", "insight", "--text", lesson],
                              cwd=ws, env=bmember(one))
                released = fire_gate(one)
                _, recalled = run(["base", "recall", "--keyword",
                                   "acceptance run"], cwd=ws, env=benv)
                readable = "one piece" in recalled
                b.add(PASS if (released == 0 and readable) else FAIL,
                      "a Member's lesson reaches the graph and is readable back out",
                      f"gate released={released == 0}, recall found it={readable}"
                      + ("" if readable else f" :: {recalled.strip()[:160]}"))

            # 11. The guard that outranks every row above it. If this fails,
            #     the run wrote the operator's own knowledge and the PASSes are
            #     worthless.
            # RED ARM for the row below. A stable hash means nothing unless a
            # changed file moves it, and the previous digest could not have
            # been moved by anything -- one of its four paths never resolved
            # and the rest were not the files being written. Proven on a probe
            # file rather than assumed, and never on the operator's own.
            probe = sandbox / "digest-probe.txt"
            probe.write_text("before", encoding="utf-8")
            probe_before = operator_digest([probe])
            probe.write_text("after", encoding="utf-8")
            b.add(PASS if operator_digest([probe]) != probe_before else FAIL,
                  "the isolation digest can actually detect a change",
                  "a changed byte moves the hash"
                  if operator_digest([probe]) != probe_before else
                  "the digest is CONSTANT across a changed file, so every "
                  "'unchanged' this harness reports is meaningless")

            # And the cross-tier arm must be watching a real file, not "absent".
            # A glob match proves a path EXISTS. It does not prove the file
            # can be read, and an existing-but-unreadable file makes
            # operator_digest record "absent" for it -- the same no-op that
            # reads green, one level down from the one that caused this row.
            # So read the bytes.
            cross = cross_tier_files()
            readable = []
            for f in cross:
                try:
                    f.read_bytes()
                    readable.append(f)
                except OSError as exc:
                    b.add(FAIL, "the other platform's tier is being watched",
                          f"{f} matched the glob but could not be read ({exc}); "
                          "the digest would record it as absent and pass "
                          "forever")
                    break
            else:
                b.add(PASS if readable else SKIP,
                      "the other platform's tier is being watched",
                      ", ".join(str(f) for f in readable) + " (read back)"
                      if readable else
                      f"no tier under {cross_tier_root()} — nothing to "
                      "cross-check on this host")

            # ARM 2 for the append-scan row below, on a COPY inside the
            # sandbox and never on the operator's own file. A scan that has
            # only ever printed CLEAN has not been shown to detect anything.
            probe_nq = sandbox / "churn-probe.nq"
            probe_nq.write_text("<a> <b> <c> .\n", encoding="utf-8")
            probe_base = churn_baseline([probe_nq])
            with open(probe_nq, "a", encoding="utf-8") as fh:
                fh.write(f"<contaminated> <by> <{sandbox}> .\n")
            planted = fingerprint_hits(probe_base, str(sandbox), (), [probe_nq])
            b.add(PASS if planted else FAIL,
                  "the append scan can actually detect a planted write",
                  "; ".join(planted) if planted else
                  "a line carrying this run's own sandbox path was appended to "
                  "a probe file and the scan did not see it, so every clean "
                  "verdict it reports below is meaningless")

            # ARM 3, THE CONTROL, and it is the one that proves this fix fixed
            # the thing it was written for. The defect being closed is that a
            # FOREIGN session's writes were read as this run's contamination.
            # So append bytes shaped like another session's triples, carrying
            # no marker of this run, and require the scan to stay clean. Arm 2
            # alone would pass just as happily if the scan reported EVERY
            # append, which is the old behaviour wearing a new name.
            ctl_nq = sandbox / "churn-control.nq"
            ctl_nq.write_text("<a> <b> <c> .\n", encoding="utf-8")
            ctl_base = churn_baseline([ctl_nq])
            with open(ctl_nq, "a", encoding="utf-8") as fh:
                fh.write(("<http://ops-sys.local/ontology#note/another-session> "
                          "<http://ops-sys.local/ontology#body> "
                          "\"a different session was working while this ran\" "
                          "<http://ops-sys.local/ontology#graph/ws/base-gbl> .\n")
                         * 25)
            foreign = fingerprint_hits(ctl_base, str(sandbox), (), [ctl_nq])
            b.add(PASS if not foreign else FAIL,
                  "the append scan ignores another session's writes",
                  f"{ctl_nq.stat().st_size} bytes of another session's triples "
                  "appended, scan stayed clean" if not foreign else
                  "the scan read a FOREIGN append as this run's contamination, "
                  "which is the exact defect this row exists to close: "
                  + "; ".join(foreign))

            # ROW A -- the exact-hash paths. Wording UNCHANGED, because it
            # still means precisely what it always meant, about precisely the
            # files it can still say it about.
            after_hash = operator_digest()
            b.add(PASS if before_hash == after_hash else FAIL,
                  "the operator's own graph and registry were never written to",
                  f"content hash {before_hash[:12]} unchanged across "
                  f"{len(operator_files())} exact-hash path(s)"
                  if before_hash == after_hash else
                  f"OPERATOR GRAPH CHANGED {before_hash[:12]} -> {after_hash[:12]}"
                  " — this run contaminated real knowledge")

            # ROW B -- the churning paths, and a SEPARATE row because it is a
            # DIFFERENT CLAIM. Row A says those files were not written to at
            # all. This one cannot say that and does not pretend to: it says
            # nothing appended carries this run's fingerprint. A single row
            # carrying both would report more than it verified, which is the
            # defect issue #62 names, one directory away.
            present = [f for f in churning_files() if f.exists()]
            if not present:
                # ABSENT IS A PASSING VALUE, so it is not allowed to read as a
                # pass. No base knowledge store here means there was nothing
                # this run could have contaminated -- true, and unmeasured.
                b.add(SKIP, "no appended write carries this run's fingerprint",
                      f"none of the {len(churning_files())} base knowledge "
                      "files exist on this host, so there was nothing for this "
                      "run to contaminate. Unmeasured, not passed.")
            else:
                hits = fingerprint_hits(churn_before, str(sandbox),
                                        (str(base_home),))
                b.add(PASS if not hits else FAIL,
                      "no appended write carries this run's fingerprint",
                      f"scanned {len(present)} churning path(s) for "
                      f"{sandbox.name}; nothing of this run's is in them"
                      if not hits else
                      "THIS RUN'S FINGERPRINT IS IN THE OPERATOR'S KNOWLEDGE: "
                      + "; ".join(hits))
        return end_base_section()
    finally:
        if a.json_out:
            Path(a.json_out).write_text(json.dumps({
                "completed": finished,
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "pythonioencoding": os.environ.get("PYTHONIOENCODING"),
                "rows": b.rows,
                "pass": b.count(PASS), "fail": b.count(FAIL),
                "blocked": b.count(BLOCKED), "skip": b.count(SKIP),
            }, indent=2), encoding="utf-8")
            print(f"\n  json summary -> {a.json_out}")
        if a.keep:
            print(f"\n  sandbox kept at {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
