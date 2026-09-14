#!/usr/bin/env bash
# lapwing lane D, issue 118: MUTATION ARM (law 38). Existing and reached are not
# evidence; only FIRING is. Break each thing these guards exist to catch and
# watch the named arms go red, with the unmutated row as the blindness control.
#
# The three clauses under test, and the question each answers:
#   M-A  check() stops PROBING and assumes `base cadre` runs.
#        -> is `extension_runs` a reading, or decoration beside the file check?
#   M-B  ensure() takes install()'s own verdict instead of re-reading.
#        -> is the re-read load-bearing, or does it always agree anyway?
#   M-C  founding REFUSES when base is absent.
#        -> is "degraded, never broken" enforced, or just described?
#
# Law 39: each mutation must fire a DIFFERENT arm, or they are one detector
# written three times. The pairing here is A4<-M-A, ADIS<-M-B, A7<-M-C.
#
# PREDICTED BEFORE RUNNING (a prediction written afterwards is not one):
#   ORIG   -> all five sets green
#   M-A    -> A4 RED, A6 RED, ADIS RED (all three read extension_runs),
#             A7 green, CONTROLS green
#   M-B    -> ADIS RED only. A4 and A6 green, because under both of those the
#             installer's verdict and the re-read AGREE, so neither can see the
#             difference. That is the whole reason ADIS exists.
#   M-C    -> A7 RED (both legs: the result flips to not-ok), everything else green
#
# MEASURED: see the matrix this script prints. Where a row disagrees with the
# prediction, the prediction is kept above rather than rewritten -- a matrix
# that only ever shows the shape it expected is not a measurement.
set -u
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
READY="$WT/src/firm/services/base_ready.py"
FOUNDING="$WT/src/firm/dashboard/founding.py"
KEEP="$(mktemp -d -t cadre-118-mutation-XXXXXX)"
trap 'cp "$KEEP/base_ready.py.orig" "$READY" 2>/dev/null; cp "$KEEP/founding.py.orig" "$FOUNDING" 2>/dev/null; rm -rf "$KEEP"' EXIT
cd "$WT" || exit 9
FAIL=0
ROWS=0

A4="test_l4_a_manifest_on_disk_over_a_dead_command_reads_as_two_facts"
A6="test_l6_an_install_that_reports_success_over_a_dead_command_is_not_ok"
ADIS="test_an_installer_that_claims_success_is_not_taken_at_its_word"
A7="test_l7_a_firm_is_founded_without_base_and_the_result_says_so or test_the_base_reading_is_taken_before_the_workspace_exists"
CONTROLS="test_l2_control_a_healthy_machine_reads_every_key_true or test_l5_control_the_same_manifest_with_a_live_command_is_ok"

cp "$READY" "$KEEP/base_ready.py.orig"
cp "$FOUNDING" "$KEEP/founding.py.orig"
READY_MD5=$(md5sum "$READY" | cut -d' ' -f1)
FOUNDING_MD5=$(md5sum "$FOUNDING" | cut -d' ' -f1)
echo "pristine base_ready.py md5: $READY_MD5"
echo "pristine founding.py  md5: $FOUNDING_MD5"
echo

run_set() {   # $1 label, $2 -k expression, $3 expected (green|red)
  local out rc n_pass n_fail
  out=$(env PYTHONPATH=src CADRE_CLAUDE_BIN=/bin/echo timeout 300 python3 -m pytest \
        tests/services/test_base_ready.py tests/test_founding_validates_base.py \
        -o addopts= -q -k "$2" 2>&1)
  rc=$?
  n_pass=$(printf '%s\n' "$out" | grep -o '[0-9]* passed' | tail -1 | cut -d' ' -f1)
  n_fail=$(printf '%s\n' "$out" | grep -o '[0-9]* failed' | tail -1 | cut -d' ' -f1)
  : "${n_pass:=0}"; : "${n_fail:=0}"
  ROWS=$((ROWS+1))
  # A selector that matched NOTHING is a void result, never a pass (law 23).
  if [ "$n_pass" = "0" ] && [ "$n_fail" = "0" ]; then
    echo "    $1: SELECTED ZERO TESTS — proves nothing"; FAIL=$((FAIL+1)); return
  fi
  echo "    $1: passed=$n_pass failed=$n_fail rc=$rc (want $3)"
  if [ "$3" = "green" ] && [ "$rc" -ne 0 ]; then
    echo "      UNEXPECTED RED"; FAIL=$((FAIL+1))
  fi
  if [ "$3" = "red" ] && [ "$rc" -eq 0 ]; then
    echo "      UNEXPECTED GREEN — the mutation never reached the clause"; FAIL=$((FAIL+1))
  fi
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

echo "ORIG (blindness control — every arm must be green here)"
run_set "A4  installed-and-dead" "$A4" green
run_set "A6  repair that did not take" "$A6" green
run_set "ADIS installer not taken at its word" "$ADIS" green
run_set "A7  founding never refuses" "$A7" green
run_set "CTRL healthy machine" "$CONTROLS" green
echo

echo "M-A  check() assumes the command runs instead of probing it"
mutate "$READY" 'result["extension_runs"] = ran.returncode == 0' \
                'result["extension_runs"] = True  # MUTANT M-A'
run_set "A4  installed-and-dead" "$A4" red
run_set "A6  repair that did not take" "$A6" red
run_set "ADIS installer not taken at its word" "$ADIS" red
run_set "A7  founding never refuses" "$A7" green
run_set "CTRL healthy machine" "$CONTROLS" green
restore
echo

echo "M-B  ensure() believes install()'s own verdict instead of the re-read"
mutate "$READY" '    after = check(workspace)
    after["install"] = installed' \
                '    after = check(workspace)
    after["ok"] = bool(installed.get("ok"))  # MUTANT M-B
    after["install"] = installed'
run_set "A4  installed-and-dead" "$A4" green
run_set "A6  repair that did not take" "$A6" green
run_set "ADIS installer not taken at its word" "$ADIS" red
run_set "A7  founding never refuses" "$A7" green
run_set "CTRL healthy machine" "$CONTROLS" green
restore
echo

echo "M-C  founding refuses to make a firm when base is absent"
mutate "$FOUNDING" '    base_before = base_ready.check()' \
                   '    base_before = base_ready.check()
    if not base_before.get("base_present"):   # MUTANT M-C
        return {"ok": False, "error": "base is not installed"}'
run_set "A4  installed-and-dead" "$A4" green
run_set "A6  repair that did not take" "$A6" green
run_set "ADIS installer not taken at its word" "$ADIS" green
run_set "A7  founding never refuses" "$A7" red
run_set "CTRL healthy machine" "$CONTROLS" green
restore
echo

echo "ROWS VISITED = $ROWS"
if [ "$ROWS" -eq 0 ]; then
  echo "VERDICT: VOID — no row ran, so nothing was measured"
  exit 2
fi
if [ "$FAIL" -eq 0 ]; then
  echo "VERDICT: PASS — every clause fires when broken and stays quiet when not"
  exit 0
fi
echo "VERDICT: FAIL — $FAIL row(s) disagreed with the prediction"
exit 1
