#!/usr/bin/env bash
# dunlin lane E, issue 115: MUTATION ARM (law 38). Existing and reached are not
# evidence; only FIRING is. Break each thing the guards exist to catch and watch
# the named arms go red, with the unmutated row as the blindness control.
#
# Law 39: the two mutations must fire DIFFERENT arms. If one mutation reddens
# both sets they are not two detectors, they are one written twice.
#
# Predicted BEFORE running (a prediction written after the fact is not one):
#   ORIG  -> everything green
#   M1    -> the four COLLISION arms red, every unreadable arm green
#   M2    -> the four UNREADABLE arms red, every collision arm green
#
# MEASURED, and the prediction was half wrong. ORIG and M2 landed as written.
# Under M1, two of the four "unreadable" arms went RED as well. They were right
# to and the prediction was not: M1 returns from `foreign_rules` before
# `parse_rule_listing` is ever reached, so an arm that gets to the parser
# THROUGH install() or doctor sits below both mutations. The set is split below
# into PARSER (independent, the pair law 39 asks for) and DOWNSTREAM (below
# both, asserted red under each). The prediction is kept rather than rewritten:
# a matrix that only ever shows the shape it expected is not a measurement.
set -u
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$WT/src/firm/services/base_extension.py"
KEEP="$(mktemp -d -t cadre-115-mutation-XXXXXX)"
trap 'rm -rf "$KEEP"' EXIT
cd "$WT" || exit 9
FAIL=0

COLLISION_ARMS="test_a_foreign_rule_in_the_graph_sets_the_collision_field or test_one_changed_character_is_still_foreign or test_the_collision_report_names_base_as_the_owner or test_doctor_reports_rules_the_manifest_did_not_write"

# The unreadable arms split in two, and the split is a FINDING rather than a
# convenience. The first run of this matrix predicted every unreadable arm
# green under M1 and two of them went red. They were right to: M1 returns from
# `foreign_rules` before `parse_rule_listing` is ever called, so an arm that
# reaches the parser THROUGH install() or doctor is downstream of both
# mutations and cannot be independent of either. The two arms that call the
# parser directly are independent, and they are what law 39 actually asks for.
# Asserting the true shape keeps the teeth; asserting the predicted shape would
# have meant bending one of them until it agreed.
PARSER_ARMS="test_the_listing_parser_refuses_what_it_cannot_read or test_the_listing_parser_refuses_a_count_that_does_not_reconcile"
DOWNSTREAM_ARMS="test_an_unreadable_graph_is_reported_as_unknown_never_as_clean or test_doctor_does_not_pass_over_a_listing_it_could_not_read"
CLEAN_ARMS="test_a_clean_graph_leaves_the_collision_field_unset or test_a_graph_copy_that_matches_this_manifest_is_not_a_collision or test_a_clean_install_prints_no_collision_block or test_doctor_passes_when_the_graph_copy_is_cadres_own or test_doctor_passes_when_the_graph_holds_no_copy"

cp "$SRC" "$KEEP/base_extension.py.orig"
ORIG_MD5=$(md5sum "$SRC" | cut -d' ' -f1)
echo "pristine md5: $ORIG_MD5"

run_set() {  # $1 = label, $2 = -k expression, $3 = expected (green|red)
  local out rc n_pass n_fail
  out=$(env PYTHONPATH=src CADRE_CLAUDE_BIN=/bin/echo timeout 300 python3 -m pytest \
        tests/services/test_base_extension.py tests/cli/test_doctor_extension_rules.py \
        -o addopts= -q -k "$2" 2>&1)
  rc=$?
  # grep -o, not a sed backreference guarded by [^0-9]: pytest's summary line
  # BEGINS with the number ("4 passed, 69 deselected"), so there is no
  # non-digit in front of it to anchor on, every count read as empty, and the
  # zero-selection guard then reported "proves nothing" over a run that had
  # genuinely selected four tests. The guard was right; the reader was blind.
  n_pass=$(printf '%s\n' "$out" | grep -o '[0-9]* passed' | tail -1 | cut -d' ' -f1)
  n_fail=$(printf '%s\n' "$out" | grep -o '[0-9]* failed' | tail -1 | cut -d' ' -f1)
  : "${n_pass:=0}"; : "${n_fail:=0}"
  # A selector that matched NOTHING is a void result, never a pass (law 23).
  if [ "$n_pass" = "0" ] && [ "$n_fail" = "0" ]; then
    echo "    $1: SELECTED ZERO TESTS — proves nothing"; FAIL=$((FAIL+1)); return
  fi
  echo "    $1: passed=$n_pass failed=$n_fail rc=$rc (want $3)"
  if [ "$3" = "green" ] && [ "$rc" -ne 0 ]; then
    echo "      UNEXPECTED RED"; FAIL=$((FAIL+1))
  fi
  if [ "$3" = "red" ] && [ "$rc" -eq 0 ]; then
    echo "      UNEXPECTED GREEN — the mutation did not reach the guard"; FAIL=$((FAIL+1))
  fi
}

restore() {
  cp "$KEEP/base_extension.py.orig" "$SRC"
  local now; now=$(md5sum "$SRC" | cut -d' ' -f1)
  [ "$now" = "$ORIG_MD5" ] || { echo "RESTORE FAILED: $now != $ORIG_MD5"; exit 3; }
}

echo
echo "=== ORIG — the blindness control. Must be GREEN on every set."
run_set "collision arms" "$COLLISION_ARMS" green
run_set "parser arms" "$PARSER_ARMS" green
run_set "downstream arms" "$DOWNSTREAM_ARMS" green
run_set "clean arms" "$CLEAN_ARMS" green

echo
echo "=== M1 — foreign_rules stops comparing (returns no findings, ever)"
python3 - "$SRC" <<'PY'
import io, sys
p = sys.argv[1]
s = io.open(p, encoding="utf-8", newline="").read()
old = "    findings: list[dict[str, Any]] = []\n    for domain, mine in manifest_domains(rendered):"
assert s.count(old) == 1, f"M1 anchor hits {s.count(old)}"
new = "    findings: list[dict[str, Any]] = []\n    return findings  # MUTANT M1\n    for domain, mine in manifest_domains(rendered):"
io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new, 1))
print("    M1 applied")
PY
run_set "collision arms" "$COLLISION_ARMS" red
run_set "parser arms" "$PARSER_ARMS" green        # independent of M1
run_set "downstream arms" "$DOWNSTREAM_ARMS" red   # sits below both, see the note above
run_set "clean arms" "$CLEAN_ARMS" green
restore

echo
echo "=== M2 — parse_rule_listing swallows an unreadable listing instead of raising"
python3 - "$SRC" <<'PY'
import io, sys
p = sys.argv[1]
s = io.open(p, encoding="utf-8", newline="").read()
old = '''    if header is None:
        raise GraphReadFailed('''
assert s.count(old) == 1, f"M2 anchor A hits {s.count(old)}"
s = s.replace(old, "    if header is None:\n        return []  # MUTANT M2\n        raise GraphReadFailed(", 1)
old2 = '''    if len(rules) != declared:
        raise GraphReadFailed('''
assert s.count(old2) == 1, f"M2 anchor B hits {s.count(old2)}"
s = s.replace(old2, "    if len(rules) != declared:\n        return rules  # MUTANT M2\n        raise GraphReadFailed(", 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("    M2 applied")
PY
run_set "parser arms" "$PARSER_ARMS" red
run_set "downstream arms" "$DOWNSTREAM_ARMS" red
run_set "collision arms" "$COLLISION_ARMS" green   # M2 does not reach these
run_set "clean arms" "$CLEAN_ARMS" green
restore

echo
echo "=== restored, and green again"
echo "md5 now: $(md5sum "$SRC" | cut -d' ' -f1)  (pristine $ORIG_MD5)"
run_set "collision arms" "$COLLISION_ARMS" green
run_set "parser arms" "$PARSER_ARMS" green
run_set "downstream arms" "$DOWNSTREAM_ARMS" green

printf '\nFAIL COUNT = %d\n' "$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
