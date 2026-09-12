"""The `__pycache__` purge in the plant-and-restore controls must actually work.

ISSUE #89. A false green appeared in a verification script: a combined control
run reported 31 passed where an isolated rerun of the same plant failed. It was
deterministic, three times out of three, so not a race.

THE MECHANISM. CPython validates a `.pyc` on the source's mtime SECOND and its
SIZE, never its content. Both live in the pyc header. Size alone is not the
trap -- writing a file bumps its mtime, and a bumped mtime invalidates the pyc
whatever the size. The trap is SAME SIZE AND THE SAME WHOLE SECOND, which is
what a fast control produces every time it plants, measures and restores inside
one second. A same-size mutation is not exotic: `True` and `None` are both four
characters, and swapping one comparison operator for another is a zero delta
every time.

AND THE CACHE IS SHARED. `cache_from_source` keys the cache on the SOURCE PATH,
not the module name, so `scripts/acceptance-e2e.py` imported as `acceptance_e2e`
by four tests and as `h` by `verify_row_conservation.sh` reads and writes one
file. A false green planted by one importer is served to a different test.

WHAT THIS FILE ASSERTS. The first two tests are the red arm and its healthy
control, run on a throwaway module rather than the real harness: the same
zero-delta mutation is INVISIBLE without the purge and SEEN with it. A red arm
with no healthy control asserted green proves nothing, so both are here and both
are measured in the same way.

The last two tests are what stops the purge rotting out of the two real
controls. They read those files and require the purge to follow every write to
the harness. Deleting a one-line `rm` is the easiest change in the world to make
by accident, and without these the suite would go on printing green.

A NOTE ON THE FIRST TEST FAILING. If the stale read ever stops reproducing, this
rule has expired -- CPython changed how it invalidates -- and somebody should
find out why rather than deleting the purge.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SECTION_11 = REPO / "tests" / "test_section_11_spawns_only_through_brun.py"
ROW_CONSERVATION = REPO / "scripts" / "verify" / "verify_row_conservation.sh"

ORIGINAL = "FLAG = True\n"
MUTATED = "FLAG = None\n"


def _load(path: Path, name: str):
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plant_a_zero_delta_mutation(tmp_path: Path) -> Path:
    """Write the module, cache it, then mutate it without moving mtime or size."""
    assert len(ORIGINAL.encode()) == len(MUTATED.encode()), (
        "this proof needs a ZERO-DELTA mutation; these two differ in length, so "
        "size alone would invalidate the cache and the test would pass for the "
        "wrong reason")
    mod = tmp_path / "probe.py"
    mod.write_text(ORIGINAL, encoding="utf-8", newline="\n")
    assert _load(mod, "probe").FLAG is True, "the baseline did not load"
    stamp = mod.stat().st_mtime
    mod.write_text(MUTATED, encoding="utf-8", newline="\n")
    os.utime(mod, (stamp, stamp))
    assert mod.read_text(encoding="utf-8") == MUTATED, "the plant did not reach disk"
    return mod


def _cache_of(mod: Path) -> Path:
    return Path(importlib.util.cache_from_source(str(mod)))


def test_a_zero_delta_mutation_is_invisible_without_the_purge(tmp_path):
    """THE RED ARM. The source on disk says None and the interpreter says True."""
    mod = _plant_a_zero_delta_mutation(tmp_path)
    assert _cache_of(mod).is_file(), (
        "no bytecode was cached, so this arm is not measuring what it claims")
    assert _load(mod, "probe").FLAG is True, (
        "the stale cache did not reproduce. Either CPython no longer validates "
        "on mtime and size, or this arm stopped planting a zero delta. Find out "
        "which before deleting any purge that depends on this.")


def test_the_same_mutation_is_seen_once_the_cache_is_purged(tmp_path):
    """THE HEALTHY CONTROL. Same plant, same second, purge, and it is seen."""
    mod = _plant_a_zero_delta_mutation(tmp_path)
    cache = _cache_of(mod)
    assert cache.is_file(), "nothing to purge, so this control proves nothing"
    cache.unlink()
    assert _load(mod, "probe").FLAG is None, (
        "the purge removed the cached bytecode and the interpreter STILL did "
        "not read the mutated source")


def test_the_planting_test_purges_after_every_write_to_the_harness():
    """`_purge_bytecode()` must follow every `HARNESS.write_text(` in the arm."""
    assert SECTION_11.is_file(), (
        f"{SECTION_11} is not there. This guard names the control it protects "
        "by path, so a rename leaves it checking nothing -- update SECTION_11 "
        "at the top of this file to the control's new name rather than "
        "deleting the guard.")
    lines = SECTION_11.read_text(encoding="utf-8").splitlines()
    writes = [i for i, ln in enumerate(lines) if "HARNESS.write_text(" in ln]
    assert len(writes) >= 2, (
        f"expected at least the plant and the restore, found {len(writes)} "
        f"writes in {SECTION_11.name}. A sweep that finds nothing reads exactly "
        "like a clean file.")
    unguarded = [i + 1 for i in writes
                 if "_purge_bytecode()" not in "\n".join(lines[i + 1:i + 4])]
    assert not unguarded, (
        f"{SECTION_11.name} writes the harness at line(s) {unguarded} without "
        "purging its bytecode afterwards. A same-size plant or restore inside "
        "one second is then invisible and the arm grades stale bytecode.")


def test_the_shell_control_purges_after_every_write_to_the_harness():
    """The shell control's purge is structural, so assert each piece by name."""
    assert ROW_CONSERVATION.is_file(), (
        f"{ROW_CONSERVATION} is not there. This guard names the control it "
        "protects by path, so a rename leaves it checking nothing -- update "
        "ROW_CONSERVATION at the top of this file to the control's new name "
        "rather than deleting the guard.")
    src = ROW_CONSERVATION.read_text(encoding="utf-8")
    required = {
        "the purge helper is defined":
            "purge_bytecode() { rm -f scripts/__pycache__/acceptance-e2e.*.pyc; }",
        "the by-path load at the top does not leave its cache behind":
            "purge_bytecode   # the by-path load above cached the pristine harness",
        "the mutant runner purges before it measures":
            "run_mutant() {  # $1 = label; mutation already applied\n  purge_bytecode\n",
        "the mutant runner purges after it restores":
            '  cp "$OUT/orig.py" scripts/acceptance-e2e.py\n  purge_bytecode\n}',
        "the exit trap purges":
            "trap 'cp \"$OUT/orig.py\" scripts/acceptance-e2e.py 2>/dev/null; "
            "purge_bytecode' EXIT",
        "the final restore purges":
            'cp "$OUT/orig.py" scripts/acceptance-e2e.py\npurge_bytecode\n',
    }
    missing = sorted(why for why, s in required.items() if s not in src)
    assert not missing, (
        f"{ROW_CONSERVATION.name} has lost part of its bytecode purge: "
        f"{missing}. It plants into the real harness and a same-size plant "
        "inside one second would then be graded against stale bytecode.")
