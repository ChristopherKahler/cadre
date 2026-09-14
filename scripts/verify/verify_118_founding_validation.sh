#!/usr/bin/env bash
# lapwing lane D, issue 118: MUTATION ARM (law 38). Existing and reached are not
# evidence; only FIRING is. Break each thing these guards exist to catch and
# watch the named arms go red, with the unmutated row as the blindness control.
#
# THIRD VERSION, after avocet's G2 of PR 127 (REWORK REQUIRED). v2's clauses
# M-A..M-D are kept unchanged. v3 adds the rework's clauses:
#   M-E1   the tier gate reports but does not stop           (F1)
#   M-E2   --version runs before the refusal returns          (F1, --version counts)
#   M-F2a  base_cadre row ok back to extension_runs           (F2)
#   M-F2b  base_cadre row detail back to extension_runs       (F2)
#   M-F2c  base_cadre_runs back to extension_runs             (F2)
#   M-F3a  the third reason branch's text                     (F3)
#   M-F3b  base_may_write_the_tier constant True              (F3)
#   M-F3c  the refusal's reason replaced                      (F3)
#   M-F4a  base_cadre_runs constant False                     (F4)
#   M-F4b  base_cadre row ok constant False                   (F4)
#   M-F4c  base row ok constant False                         (F4)
#   M-F6a  the helper quotes the last line                    (F6)
#   M-F6b  the --version site quotes its last line inline     (F6)
#   M-F6c  the helper quotes the FIRST unindented line        (F6, added: see below)
#   M-F5   unused from-import of _installed_path in _tier     (F5)
#
# PREDICTED BEFORE ANY CODE, in the brief's pre-registration (R red, . green),
# arms in this order: A4 A11 A7 NOREPAIR CTRL REFUSE NATIVE ELSEWHERE TRUE INFORM TIERAST
#   ORIG ...........   M-A RR.......R.   M-B .R..R...R..   M-C ..R........
#   M-D  ...R.......   M-E1/M-E2/M-F3b/M-F3c .....R.....
#   M-F2a/M-F2b/M-F2c/M-F3a .......R...   M-F4a/M-F4b/M-F4c ........R..
#   M-F6a/M-F6b .........R.   M-F5 ..........R
#   M-F6c .........R. (only through the [traceback] leg). Added after the first
#   run and predicted before this row ran, in the brief's ADDENDUM: under M-F6a
#   the traceback leg stayed green, because a traceback's last line is its last
#   unindented line, so no row proved "last unindented" over "first unindented".
#
# THE INSTRUMENT. One pytest run per row over both files, with a JUnit report.
# Every result is attributed to its arm by TEST NAME, never by position (law 33).
# Each arm must visit exactly its registered tests: a renamed, missing, skipped
# or duplicated test voids the row rather than reading as green (laws 23, 48).
# The unmutated row must also exit 0 over BOTH whole files. pytest rc outside
# 0/1 voids a row. Sources are md5-restored after every row, and the script's
# rc is assigned at the end, never inherited (law 27).
set -u
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
READY="$WT/src/firm/services/base_ready.py"
FOUNDING="$WT/src/firm/dashboard/founding.py"
KEEP="$(mktemp -d -t cadre-118-mutation-XXXXXX)"
trap 'cp "$KEEP/base_ready.py.orig" "$READY" 2>/dev/null; cp "$KEEP/founding.py.orig" "$FOUNDING" 2>/dev/null; rm -rf "$KEEP"' EXIT
cd "$WT" || exit 9
FAIL=0
VOID=0
ROWS=0

cp "$READY" "$KEEP/base_ready.py.orig"
cp "$FOUNDING" "$KEEP/founding.py.orig"
READY_MD5=$(md5sum "$READY" | cut -d' ' -f1)
FOUNDING_MD5=$(md5sum "$FOUNDING" | cut -d' ' -f1)
echo "script md5:              $(md5sum "${BASH_SOURCE[0]}" | cut -d' ' -f1)"
echo "pristine base_ready.py md5: $READY_MD5"
echo "pristine founding.py  md5: $FOUNDING_MD5"
echo "python: $(python3 --version 2>&1); pytest: $(python3 -m pytest --version 2>&1 | head -1)"
echo "arms:   A4 A11 A7 NOREPAIR CTRL REFUSE NATIVE ELSEWHERE TRUE INFORM TIERAST"
echo

grade() {     # $1 row label, $2 predicted cells (11 of R/.), $3 "whole" when rc must be 0
  local xml="$KEEP/row.xml" rc g
  rm -f "$xml"
  env PYTHONPATH=src CADRE_CLAUDE_BIN=/bin/echo timeout 600 python3 -m pytest \
      tests/services/test_base_ready.py tests/test_founding_validates_base.py \
      -o addopts= -q -p no:cacheprovider --junitxml="$xml" > "$KEEP/row.log" 2>&1
  rc=$?
  ROWS=$((ROWS+1))
  python3 - "$xml" "$1" "$2" "$rc" "${3:-}" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

xml, label, want, rc, whole = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
ARMS = [
    ("A4", ["test_l4_a_manifest_in_the_firms_tier_over_a_dead_command_reads_as_two_facts"]),
    ("A11", ["test_l11_an_install_in_the_operators_tier_does_not_count_for_the_firm",
             "test_l8_an_install_only_in_the_operators_tier_is_reported_as_not_running"]),
    ("A7", ["test_l7_a_firm_is_founded_without_base_and_the_result_says_so",
            "test_the_base_reading_is_taken_before_the_workspace_exists"]),
    ("NOREPAIR", ["test_ensure_never_installs_into_any_tier",
                  "test_founding_writes_nothing_into_the_operators_tier"]),
    ("CTRL", ["test_l2_control_the_manifest_in_the_firms_tier_reads_every_key_true",
              "test_l5_control_the_same_manifest_with_a_live_command_is_ok",
              "test_ensure_on_a_firm_whose_command_runs_reports_nothing_to_repair"]),
    ("REFUSE", ["test_a_base_this_host_cannot_run_is_refused_before_anything_runs[firm]",
                "test_a_base_this_host_cannot_run_is_refused_before_anything_runs[no_firm]",
                "test_a_refused_base_reads_unhealthy_on_both_readiness_rows",
                "test_founding_with_a_refused_base_never_reports_the_command_running"]),
    ("NATIVE", ["test_control_a_base_this_host_can_run_is_let_through_and_probed"]),
    ("ELSEWHERE", ["test_a_command_resolved_from_outside_the_firms_tier_is_not_an_install",
                   "test_a_command_resolved_elsewhere_is_not_shown_as_installed",
                   "test_founding_does_not_report_a_command_resolved_elsewhere_as_running"]),
    ("TRUE", ["test_a_healthy_firm_tier_reads_healthy_on_both_readiness_rows",
              "test_founding_reports_the_command_running_when_the_firms_tier_is_healthy"]),
    ("INFORM", ["test_the_reason_quotes_the_line_that_names_the_failure[handler_not_found]",
                "test_the_reason_quotes_the_line_that_names_the_failure[traceback]",
                "test_the_reason_quotes_the_line_that_names_the_failure[unknown_command]",
                "test_a_base_that_does_not_run_is_quoted_by_the_line_that_names_it"]),
    ("TIERAST", ["test_the_tier_is_decided_in_one_place_and_it_is_the_firms"]),
]
if len(want) != len(ARMS) or set(want) - {"R", "."}:
    print(f"{label}: PREDICTION MALFORMED {want!r}")
    sys.exit(2)
if rc not in (0, 1):
    print(f"{label}: VOID - pytest rc {rc} (interrupted, usage, collection or timeout)")
    sys.exit(2)
path = Path(xml)
if not path.is_file():
    print(f"{label}: VOID - pytest wrote no report")
    sys.exit(2)

cases: dict[str, list[tuple[str, str]]] = {}
for case in ET.parse(path).getroot().iter("testcase"):
    state, msg = "passed", ""
    for tag in ("failure", "error"):
        el = case.find(tag)
        if el is not None:
            state = "failed"
            msg = ((el.get("message") or "").splitlines() or [""])[0]
    if state == "passed" and case.find("skipped") is not None:
        state = "skipped"
    cases.setdefault(case.get("name", ""), []).append((state, msg))

void, cells, notes = False, [], []
for (arm, names), expected in zip(ARMS, want):
    seen = [n for n in names if n in cases]
    if len(seen) != len(names):
        notes.append(f"{arm} VISITED {len(seen)} of {len(names)}; missing {sorted(set(names) - set(seen))}")
        void = True
        cells.append("?")
        continue
    if any(len(cases[n]) != 1 for n in names):
        notes.append(f"{arm} has a duplicated test name, so attribution by name is unsound")
        void = True
        cells.append("?")
        continue
    states = [cases[n][0][0] for n in names]
    if "skipped" in states:
        notes.append(f"{arm} has a SKIPPED test, which proves nothing")
        void = True
        cells.append("?")
        continue
    cells.append("R" if "failed" in states else ".")

total = sum(len(v) for v in cases.values())
print(f"{label:<6} want {' '.join(want)}")
print(f"{'':<6} got  {' '.join(cells)}   (pytest rc {rc}, {total} results read by name)")
for note in notes:
    print(f"       VOID: {note}")
for name in sorted(cases):
    for state, msg in cases[name]:
        if state == "failed":
            print(f"       red: {name}: {msg}")
if void:
    sys.exit(2)
if whole == "whole" and rc != 0:
    print("       UNEXPECTED: the blindness control must pass both whole files")
    sys.exit(1)
off = [arm for (arm, _), got, exp in zip(ARMS, cells, want) if got != exp]
if off:
    print(f"       NOT AS PREDICTED: {off}")
    sys.exit(1)
print("       as predicted")
sys.exit(0)
PY
  g=$?
  if [ "$g" -eq 2 ]; then VOID=$((VOID+1)); elif [ "$g" -ne 0 ]; then FAIL=$((FAIL+1)); fi
}

mutate() {    # $1 file, $2 exact old text, $3 exact new text
  python3 - "$1" "$2" "$3" <<'PY'
import io, sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
text = io.open(path, encoding="utf-8").read()
hits = text.count(old)
if hits != 1:
    print(f"MUTATION ANCHOR matched {hits} times, expected 1: {old!r}")
    sys.exit(3)
io.open(path, "w", encoding="utf-8", newline="\n").write(text.replace(old, new))
PY
  local rc=$?
  [ "$rc" -eq 0 ] || { echo "MUTATION FAILED TO APPLY (rc $rc) — matrix abandoned"; exit 3; }
}

restore() {
  cp "$KEEP/base_ready.py.orig" "$READY"
  cp "$KEEP/founding.py.orig" "$FOUNDING"
  local a b
  a=$(md5sum "$READY" | cut -d' ' -f1); b=$(md5sum "$FOUNDING" | cut -d' ' -f1)
  if [ "$a" != "$READY_MD5" ] || [ "$b" != "$FOUNDING_MD5" ]; then
    echo "RESTORE FAILED — the tree is not as it was. Stopping."; exit 3
  fi
}

row() {       # $1 label, $2 file, $3 old, $4 new, $5 prediction
  mutate "$2" "$3" "$4"
  grade "$1" "$5"
  restore
}

grade ORIG "..........." whole

row M-A "$READY" 'result["extension_runs"] = ran.returncode == 0' \
                 'result["extension_runs"] = True  # MUTANT M-A' "RR.......R."
row M-B "$READY" 'env=_env(workspace),' 'env=_env(None),  # MUTANT M-B' ".R..R...R.."
row M-C "$FOUNDING" '    base_before = base_ready.check()' \
                    '    base_before = base_ready.check()
    if not base_before.get("base_present"):   # MUTANT M-C
        return {"ok": False, "error": "base is not installed"}' "..R........"
row M-D "$READY" '    state["repair"] = (
        "founding cannot install' \
                 '    from firm.services import base_extension as _be; _be.install()  # MUTANT M-D
    state["repair"] = (
        "founding cannot install' "...R......."
row M-E1 "$READY" 'if not may_run:' 'if False:  # MUTANT M-E1' ".....R....."
row M-E2 "$READY" 'result["reason"] = refusal' \
                  'run_utf8([base, "--version"], capture_output=True, timeout=_TIMEOUT_SEC, env=_env(None), cwd=cwd or None, stdin=subprocess.DEVNULL); result["reason"] = refusal  # MUTANT M-E2' ".....R....."
row M-F2a "$FOUNDING" '"ok": bool(base_state.get("ok")),' \
                      '"ok": bool(base_state.get("extension_runs")),  # MUTANT M-F2a' ".......R..."
row M-F2b "$FOUNDING" 'if base_state.get("ok")' \
                      'if base_state.get("extension_runs")  # MUTANT M-F2b' ".......R..."
row M-F2c "$FOUNDING" '"base_cadre_runs": bool(base_state.get("ok")),' \
                      '"base_cadre_runs": bool(base_state.get("extension_runs")),  # MUTANT M-F2c' ".......R..."
row M-F3a "$READY" '"somewhere this check cannot see")' '"MUTANT M-F3a")' ".......R..."
row M-F3b "$READY" 'result["base_may_write_the_tier"] = bool(may_run)' \
                   'result["base_may_write_the_tier"] = True  # MUTANT M-F3b' ".....R....."
row M-F3c "$READY" 'result["reason"] = refusal' 'result["reason"] = "MUTANT M-F3c"' ".....R....."
row M-F4a "$FOUNDING" '"base_cadre_runs": bool(base_state.get("ok")),' \
                      '"base_cadre_runs": False,  # MUTANT M-F4a' "........R.."
row M-F4b "$FOUNDING" '"ok": bool(base_state.get("ok")),' '"ok": False,  # MUTANT M-F4b' "........R.."
row M-F4c "$FOUNDING" '"ok": bool(base_state.get("base_present") and base_state.get("base_runs")),' \
                      '"ok": False,  # MUTANT M-F4c' "........R.."
row M-F6a "$READY" 'return (flush[-1] if flush else lines[-1]).strip()[:200]' \
                   'return lines[-1].strip()[:200]  # MUTANT M-F6a' ".........R."
row M-F6b "$READY" '+ _names_the_failure(probe))' \
                   '+ (probe.stderr or probe.stdout).strip().splitlines()[-1][:200])  # MUTANT M-F6b' ".........R."
row M-F6c "$READY" 'return (flush[-1] if flush else lines[-1]).strip()[:200]' \
                   'return (flush[0] if flush else lines[-1]).strip()[:200]  # MUTANT M-F6c' ".........R."
row M-F5 "$READY" 'from firm.services.graph_isolation import tier_extensions_dir' \
                  'from firm.services.base_extension import _installed_path  # MUTANT M-F5
    from firm.services.graph_isolation import tier_extensions_dir' "..........R"

LEFT=$(grep -rn "MUTANT" src tests | wc -l)
echo
echo "ROWS VISITED = $ROWS (want 20); MUTANT markers left in src and tests = $LEFT"
if [ "$ROWS" -ne 20 ] || [ "$LEFT" -ne 0 ]; then
  echo "VERDICT: VOID — the matrix did not run whole, or a mutation survived restore"
  exit 2
fi
if [ "$VOID" -ne 0 ]; then
  echo "VERDICT: VOID — $VOID row(s) could not be read, so they measured nothing"
  exit 2
fi
if [ "$FAIL" -eq 0 ]; then
  echo "VERDICT: PASS — every clause fires when broken and stays quiet when not"
  exit 0
fi
echo "VERDICT: FAIL — $FAIL row(s) disagreed with the prediction"
exit 1
