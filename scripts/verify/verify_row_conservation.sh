#!/bin/bash
# Row conservation, proved by running the harness BOTH ways and then breaking
# the guard on purpose.
#
# THE DEFECT. scripts/acceptance-e2e.py's base-engine section has eighteen rows
# and several arms that run none of them. The skipped arms recorded ONE row and
# dropped the other seventeen: they did not fail, they ceased to exist. So the
# same harness wrote a forty-one-row scoreboard on a host with a native `base`
# and a twenty-three-row scoreboard on a host without one, and nothing in the
# shorter one said the difference was eighteen unmeasured rows rather than a
# smaller job. report() then returned 0, so the shorter run EXITED CLEAN.
#
# WHAT THIS ASSERTS. Both arms record the same row NAMES, in the same order;
# the executed arm exits 0 and the skipped arm exits 2; and BASE_SECTION_ROWS
# and the code cannot drift apart without a FAIL row saying which name moved.
#
# tests/test_row_conservation.py is the cheap half and runs in CI. This is the
# expensive half: it runs the real harness four times and takes a few minutes.
#
#   bash scripts/verify/verify_row_conservation.sh
#
# Needs a `base` on PATH built for THIS platform. A base built for another
# platform runs fine under interop, ignores a POSIX BASE_HOME, and would write
# the operator's real tier -- so this refuses rather than measure that.
set -u
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$REPO" || exit 9
PY=${PY:-$REPO/.venv/bin/python}
OUT=${OUT:-/tmp/cadre-row-conservation}
rm -rf "$OUT"; mkdir -p "$OUT"
fails=0

# Issue #89. CPython validates a `.pyc` on the source's mtime SECOND and its
# SIZE, never its content, and the cache is keyed on the SOURCE PATH rather than
# the module name -- so the by-path load below (which imports the harness as
# `h`) and every pytest test that imports it as `acceptance_e2e` share ONE cache
# file. A plant that keeps the byte count and lands in the same whole second is
# invisible, and the stale bytecode is then served to a different test entirely.
# Both mutants below change the size today, which is luck rather than a
# safeguard: any same-size edit is a zero delta, and an operator swap or `True`
# for `None` is same-size every time. Called after EVERY write to the harness,
# restores included.
purge_bytecode() { rm -f scripts/__pycache__/acceptance-e2e.*.pyc; }

B=$(command -v base || true)
[ -n "$B" ] || { echo "ABORT: no base on PATH; run A cannot be measured"; exit 9; }
case "$(head -c 4 "$B" | od -An -c | tr -d ' ')" in
  *177ELF*|*MZ*|*cafebabe*) echo "run A base: $B ($($B --version 2>&1 | head -1))" ;;
  *) echo "ABORT: cannot identify $B as a native binary"; exit 9 ;;
esac

# A PATH with no `base` AND no empty component. An empty component means "the
# current directory", and that is how the first attempt at this found a
# directory named `base` inside the sandbox and took the harness down with
# PermissionError -- which is how the missing OSError arm in run() was found.
CLEAN=$(printf '%s' "$PATH" | tr ':' '\n' \
        | while read -r d; do [ -n "$d" ] && [ ! -x "$d/base" ] && printf '%s\n' "$d"; done \
        | paste -sd:)
case ":$CLEAN:" in *::*) echo "ABORT: the clean PATH has an empty component"; exit 9;; esac
env PATH="$CLEAN" sh -c 'command -v base' >/dev/null 2>&1 \
  && { echo "ABORT: base still resolves on the clean PATH"; exit 9; }

echo
echo "=== RUN A — native base on PATH (the executed arm) ==="
timeout 2400 "$PY" scripts/acceptance-e2e.py --json "$OUT/a.json" > "$OUT/a.log" 2>&1
rcA=$?; echo "exit $rcA"; grep -E "USABLE|NOT ESTABLISHED" "$OUT/a.log" | tail -1

echo
echo "=== RUN B — no base on PATH (the skipped arm) ==="
env PATH="$CLEAN" timeout 2400 "$PY" scripts/acceptance-e2e.py --json "$OUT/b.json" \
    > "$OUT/b.log" 2>&1
rcB=$?; echo "exit $rcB"; grep -E "USABLE|NOT ESTABLISHED" "$OUT/b.log" | tail -1

echo
echo "=== CONSERVATION, read from the JSON and not from the log ==="
"$PY" - "$OUT/a.json" "$OUT/b.json" "$rcA" "$rcB" <<'PYEOF'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("h", "scripts/acceptance-e2e.py")
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
declared = h.BASE_SECTION_ROWS
a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
rcA, rcB = int(sys.argv[3]), int(sys.argv[4])
bad = 0
print(f"  BASE_SECTION_ROWS declares {len(declared)} rows")
for label, d, rc in (("RUN A (executed)", a, rcA), ("RUN B (skipped)", b, rcB)):
    base = [r for r in d["rows"] if r["name"] in declared]
    tally = {}
    for r in base:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    missing = [n for n in declared if n not in {r["name"] for r in base}]
    print(f"  {label}: exit {rc}, {len(d['rows'])} rows, "
          f"{len(base)}/{len(declared)} base rows, {tally}, completed={d.get('completed')}")
    if missing:
        print(f"      *** DECLARED BUT NOT RECORDED: {missing}"); bad += 1
na = [r["name"] for r in a["rows"]]
nb = [r["name"] for r in b["rows"]]
only_a = [n for n in na if n not in nb]
only_b = [n for n in nb if n not in na]
print(f"  in A and not B: {only_a or 'NONE'}")
print(f"  in B and not A: {only_b or 'NONE'}")
if only_a or only_b or len(na) != len(nb):
    print("  *** ROWS STILL VANISH BETWEEN THE TWO ARMS"); bad += 1
if rcA != 0:
    print(f"  *** run A measured everything and exited {rcA}, want 0"); bad += 1
if rcB != 2:
    print(f"  *** run B skipped rows and exited {rcB}, want 2"); bad += 1
sys.exit(1 if bad else 0)
PYEOF
[ $? -ne 0 ] && fails=$((fails + 1))
purge_bytecode   # the by-path load above cached the pristine harness

echo
echo "=== MUTANTS — the conservation check must go red in BOTH directions ==="
cp scripts/acceptance-e2e.py "$OUT/orig.py"
trap 'cp "$OUT/orig.py" scripts/acceptance-e2e.py 2>/dev/null; purge_bytecode' EXIT

run_mutant() {  # $1 = label; mutation already applied
  purge_bytecode
  timeout 2400 "$PY" scripts/acceptance-e2e.py --json "$OUT/m.json" > "$OUT/m.log" 2>&1
  rc=$?
  if grep -q "recorded every row it declares" "$OUT/m.log" && [ "$rc" -ne 0 ]; then
    echo "  RED   $1  (exit $rc)"
    grep -A3 "recorded every row it declares" "$OUT/m.log" | head -4 | sed 's/^/          /'
  else
    echo "  GREEN $1  (exit $rc)  *** MUTANT SURVIVED - THE GUARD IS INERT ***"
    fails=$((fails + 1))
  fi
  cp "$OUT/orig.py" scripts/acceptance-e2e.py
  purge_bytecode
}

# 1. A name leaves the declared list. The code then emits a row the list does
#    not declare. Anchored on the leading newline: without it the four-space
#    list entry also matches the tail of the eighteen-space b.add argument.
"$PY" - <<'PYEOF' || { echo "  mutation 1 did not apply"; fails=$((fails + 1)); }
from pathlib import Path
p = Path("scripts/acceptance-e2e.py"); t = p.read_text(encoding="utf-8")
target = '\n    "the firm\'s base domain carries a live rule",\n'
assert t.count(target) == 1, f"target occurs {t.count(target)} times"
p.write_text(t.replace(target, "\n", 1), encoding="utf-8")
PYEOF
run_mutant "a name is dropped from BASE_SECTION_ROWS"

# 2. A b.add site stops emitting its declared row. This is the original defect
#    aimed at one row instead of a whole section.
"$PY" - <<'PYEOF' || { echo "  mutation 2 did not apply"; fails=$((fails + 1)); }
from pathlib import Path
p = Path("scripts/acceptance-e2e.py"); t = p.read_text(encoding="utf-8")
target = '                  "the firm\'s base domain carries a live rule",\n'
assert t.count(target) == 1, f"target occurs {t.count(target)} times"
p.write_text(t.replace(target, '                  "a row the list never declared",\n', 1),
             encoding="utf-8")
PYEOF
run_mutant "a b.add site stops emitting its declared row"

cp "$OUT/orig.py" scripts/acceptance-e2e.py
purge_bytecode
if cmp -s "$OUT/orig.py" scripts/acceptance-e2e.py; then
  echo "  restored byte for byte"
else
  echo "  *** RESTORE FAILED — scripts/acceptance-e2e.py is not what it was"
  fails=$((fails + 1))
fi

echo
[ "$fails" -ne 0 ] && { echo "ROW CONSERVATION PROOF FAILED: $fails"; exit 1; }
echo "ROW CONSERVATION PROOF PASSED"
