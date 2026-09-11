#!/usr/bin/env python3
"""Proof for the isolation append scan: every arm is SEEN red, not argued.

The cheap half of this proof is tests/test_isolation_append_scan.py, which runs
in CI. This is the expensive half. It breaks the scan three different ways and
requires a DIFFERENT row to go red for each break, because three arms that all
fail together are one arm wearing three names.

    mutant 1  the scan never reports anything
              -> the planted-write arm must go RED
              -> and the foreign-append control must STAY GREEN, which is the
                 whole reason the planted-write arm has to exist

    mutant 2  the scan reports every append
              -> the foreign-append control must go RED
              -> and the planted-write arm must STAY GREEN, which is the whole
                 reason the control has to exist

    mutant 3  the compaction fallback matches the shared markers too
              -> the stale-BASE_HOME arm must go RED
              -> BASE_HOME is a FIXED path, identical on every run on this
                 host, so over a whole file a previous run's leftovers read as
                 today's contamination

Root is derived from this file's own location and REFUSED if it is not a git
work tree, exit 91. Nothing here touches the operator's files: every check runs
on a file this script created under a temporary directory.
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if not (ROOT / ".git").exists():
    print(f"REFUSE: {ROOT} has no .git, so this is not the tree you meant to "
          "measure. Run it from inside a checkout.")
    raise SystemExit(91)
rc = subprocess.run(["git", "-C", str(ROOT), "rev-parse",
                     "--is-inside-work-tree"],
                    capture_output=True, text=True, timeout=60)
if rc.returncode != 0 or rc.stdout.strip() != "true":
    print(f"REFUSE: {ROOT} is not a git work tree.")
    raise SystemExit(91)

HARNESS = ROOT / "scripts" / "acceptance-e2e.py"
if not HARNESS.is_file():
    print(f"REFUSE: no harness at {HARNESS}.")
    raise SystemExit(92)

print(f"ROOT {ROOT}")
print(f"HARNESS {HARNESS}  md5={hashlib.md5(HARNESS.read_bytes()).hexdigest()}")
print()

SRC = HARNESS.read_text(encoding="utf-8")
RUN = "/tmp/cadre-accept-abc123"
FIXED = "/tmp/cadre-accept-base"
FOREIGN = ('<http://ops-sys.local/ontology#note/another-session> '
           '<http://ops-sys.local/ontology#body> '
           '"a different session was working while this ran" '
           '<http://ops-sys.local/ontology#graph/ws/base-gbl> .\n')

MUTANTS = {
    "the scan never reports anything": (
        "            if m and m in blob:",
        "            if False:"),
    "the scan reports every append": (
        "            if m and m in blob:",
        "            if blob.strip():"),
    "the fallback matches the shared markers too": (
        '            window, markers, how = data, (run_marker,), "whole file, compacted"',
        '            window, markers, how = (data, (run_marker,) + shared_markers,\n'
        '                                    "whole file, compacted")'),
}

fails = 0
work = Path(tempfile.mkdtemp(prefix="godwit-isolation-proof-"))


def row(ok: bool, label: str, detail: str = "") -> bool:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}   {label}")
    if detail:
        print(f"         {detail}")
    if not ok:
        fails += 1
    return ok


def load(src: str, name: str):
    p = work / f"{name}.py"
    p.write_text(src, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def checks(mod, tag: str) -> dict[str, bool]:
    """Four questions, each on a file this function created."""
    d = work / tag
    d.mkdir(parents=True, exist_ok=True)
    out = {}

    f = d / "planted.nq"
    f.write_text("<a> <b> <c> .\n", encoding="utf-8")
    b = mod.churn_baseline([f])
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"<contaminated> <by> <{RUN}> .\n")
    out["planted write is detected"] = bool(
        mod.fingerprint_hits(b, RUN, (FIXED,), [f]))

    f = d / "foreign.nq"
    f.write_text("<a> <b> <c> .\n", encoding="utf-8")
    b = mod.churn_baseline([f])
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(FOREIGN * 25)
    out["foreign append is ignored"] = not mod.fingerprint_hits(
        b, RUN, (FIXED,), [f])

    f = d / "stale.nq"
    f.write_text("<a> <b> <c> .\n" * 500, encoding="utf-8")
    b = mod.churn_baseline([f])
    f.write_text(f"<last> <week> <{FIXED}> .\n", encoding="utf-8")
    out["stale BASE_HOME after compaction is ignored"] = not mod.fingerprint_hits(
        b, RUN, (FIXED,), [f])

    f = d / "compacted.nq"
    f.write_text("<a> <b> <c> .\n" * 500, encoding="utf-8")
    b = mod.churn_baseline([f])
    f.write_text(f"<today> <wrote> <{RUN}> .\n", encoding="utf-8")
    out["run marker survives a compaction"] = bool(
        mod.fingerprint_hits(b, RUN, (FIXED,), [f]))
    return out


try:
    print("=== THE REAL SCAN: all four must hold ===")
    real = checks(load(SRC, "real"), "real")
    for k, v in real.items():
        row(v, k)

    print()
    print("=== THE MUTANTS: each must break a DIFFERENT row ===")
    expected_red = {
        "the scan never reports anything": "planted write is detected",
        "the scan reports every append": "foreign append is ignored",
        "the fallback matches the shared markers too":
            "stale BASE_HOME after compaction is ignored",
    }
    for name, (old, new) in MUTANTS.items():
        n = SRC.count(old)
        if not row(n == 1, f"mutation anchor found exactly once: {name}",
                   f"count={n}"):
            continue
        got = checks(load(SRC.replace(old, new, 1), name.replace(" ", "_")),
                     name.replace(" ", "_"))
        target = expected_red[name]
        row(got[target] is False,
            f"{name} -> {target!r} goes RED",
            "still green, so that arm proves nothing" if got[target] else "")
        others = [k for k in got if k != target and real[k] and got[k]]
        row(bool(others),
            f"{name} -> at least one other row stays GREEN",
            f"still green: {', '.join(others)}" if others else
            "every row broke together, so they are one arm with three names")

    print()
    print("=== THE SCAN IS NOT VACUOUS ON THE REAL WATCHED SET ===")
    mod = load(SRC, "real2")
    row(bool(mod.churning_files()), "churning_files() is not empty",
        ", ".join(p.name for p in mod.churning_files()))
    overlap = set(mod.operator_files()) & set(mod.churning_files())
    row(not overlap, "the two tiers do not overlap",
        "" if not overlap else str(sorted(str(p) for p in overlap)))
    row("no appended write carries this run's fingerprint"
        in mod.BASE_SECTION_ROWS, "the scanned row is declared")
    row("the operator's own graph and registry were never written to"
        in mod.BASE_SECTION_ROWS, "the exact-hash row kept its wording")
finally:
    shutil.rmtree(work, ignore_errors=True)

print()
if fails:
    print(f"ISOLATION SCAN PROOF FAILED: {fails}")
    sys.exit(1)
print("ISOLATION SCAN PROOF PASSED: every arm seen red on its own mutant")
