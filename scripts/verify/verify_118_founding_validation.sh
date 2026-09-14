#!/usr/bin/env bash
# lapwing lane D, issue 118: MUTATION ARM (law 38). Existing and reached are not
# evidence; only FIRING is. Break each thing these guards exist to catch and
# watch the named arms go red, with the unmutated row as the blindness control.
#
# SECOND VERSION. The first matrix pinned a repair that installed the extension
# into the operator's tier. After #117 every Member runs with the firm's tier,
# and a three-arm probe on the real base showed that repair changed no Member's
# exit code (operator tier installed -> rc 0; firm tier empty -> 127; firm tier
# with identical bytes -> 0). The repair was removed on that measurement, so its
# arms (M-B "believe the installer", L6, ADIS) are retired here, not bent green.
#
# The four clauses under test:
#   M-A  check() stops PROBING and assumes `base cadre` runs.
#   M-B  check() probes with the OPERATOR's BASE_HOME instead of the firm's --
#        the exact regression this lane shipped once already.
#   M-C  founding REFUSES when base is absent.
#   M-D  ensure() puts the removed repair back and calls install().
#
# PREDICTED BEFORE RUNNING (a prediction written afterwards is not one):
#   ORIG  -> all five sets green
#   M-A   -> A4 RED, A11 RED (both read extension_runs); A7, NOREPAIR, CTRL green
#   M-B   -> A11 RED, CTRL RED (the healthy controls only pass when the FIRM
#            tier is probed); A4 green (dead either way), A7 green, NOREPAIR green
#   M-C   -> A7 RED only
#   M-D   -> NOREPAIR RED only
#
# MEASURED: see the matrix this prints. A row that disagrees with the prediction
# leaves the prediction above untouched.
set -u
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
READY="$WT/src/firm/services/base_ready.py"
FOUNDING="$WT/src/firm/dashboard/founding.py"
KEEP="$(mktemp -d -t cadre-118-mutation-XXXXXX)"
trap 'cp "$KEEP/base_ready.py.orig" "$READY" 2>/dev/null; cp "$KEEP/founding.py.orig" "$FOUNDING" 2>/dev/null; rm -rf "$KEEP"' EXIT
cd "$WT" || exit 9
FAIL=0
ROWS=0

A4="test_l4_a_manifest_in_the_firms_tier_over_a_dead_command_reads_as_two_facts"
A11="test_l11_an_install_in_the_operators_tier_does_not_count_for_the_firm or test_l8_an_install_only_in_the_operators_tier_is_reported_as_not_running"
A7="test_l7_a_firm_is_founded_without_base_and_the_result_says_so or test_the_base_reading_is_taken_before_the_workspace_exists"
NOREPAIR="test_ensure_never_installs_into_any_tier or test_founding_writes_nothing_into_the_operators_tier"
CTRL="test_l2_control_the_manifest_in_the_firms_tier_reads_every_key_true or test_l5_control_the_same_manifest_with_a_live_command_is_ok or test_ensure_on_a_firm_whose_command_runs_reports_nothing_to_repair"

cp "$READY" "$KEEP/base_ready.py.orig"
cp "$FOUNDING" "$KEEP/founding.py.orig"
READY_MD5=$(md5sum "$READY" | cut -d' ' -f1)
FOUNDING_MD5=$(md5sum "$FOUNDING" | cut -d' ' -f1)
echo "pristine base_ready.py md5: $READY_MD5"
echo "pristine founding.py  md5: $FOUNDING_MD5"
echo

run_set() {   # $1 label, $2 -k expression, $3 expected (green|red)
  local out rc n_pass n_fail n_err
  out=$(env PYTHONPATH=src CADRE_CLAUDE_BIN=/bin/echo timeout 300 python3 -m pytest \
        tests/services/test_base_ready.py tests/test_founding_validates_base.py \
        -o addopts= -q -k "$2" 2>&1)
  rc=$?
  n_pass=$(printf '%s\n' "$out" | grep -o '[0-9]* passed' | tail -1 | cut -d' ' -f1)
  n_fail=$(printf '%s\n' "$out" | grep -o '[0-9]* failed' | tail -1 | cut -d' ' -f1)
  n_err=$(printf '%s\n' "$out" | grep -o '[0-9]* error' | tail -1 | cut -d' ' -f1)
  : "${n_pass:=0}"; : "${n_fail:=0}"; : "${n_err:=0}"
  ROWS=$((ROWS+1))
  if [ "$n_pass" = "0" ] && [ "$n_fail" = "0" ] && [ "$n_err" = "0" ]; then
    echo "    $1: SELECTED ZERO TESTS — proves nothing"; FAIL=$((FAIL+1)); return
  fi
  echo "    $1: passed=$n_pass failed=$n_fail errors=$n_err rc=$rc (want $3)"
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

all_sets() {  # $1..$5 expectations for A4 A11 A7 NOREPAIR CTRL
  run_set "A4       installed-and-dead in the firm tier" "$A4" "$1"
  run_set "A11      operator-tier install does not count" "$A11" "$2"
  run_set "A7       founding never refuses" "$A7" "$3"
  run_set "NOREPAIR nothing is installed anywhere" "$NOREPAIR" "$4"
  run_set "CTRL     healthy firm tier" "$CTRL" "$5"
}

echo "ORIG (blindness control — every arm must be green here)"
all_sets green green green green green
echo

echo "M-A  check() assumes the command runs instead of probing it"
mutate "$READY" 'result["extension_runs"] = ran.returncode == 0' \
                'result["extension_runs"] = True  # MUTANT M-A'
all_sets red red green green green
restore
echo

echo "M-B  check() probes base cadre with the OPERATOR's BASE_HOME"
mutate "$READY" '                           timeout=_TIMEOUT_SEC, env=_env(workspace),' \
                '                           timeout=_TIMEOUT_SEC, env=_env(None),  # MUTANT M-B'
all_sets green red green green red
restore
echo

echo "M-C  founding refuses to make a firm when base is absent"
mutate "$FOUNDING" '    base_before = base_ready.check()' \
                   '    base_before = base_ready.check()
    if not base_before.get("base_present"):   # MUTANT M-C
        return {"ok": False, "error": "base is not installed"}'
all_sets green green red green green
restore
echo

echo "M-D  ensure() puts the removed operator-tier repair back"
mutate "$READY" '    state["repair"] = (
        "founding cannot install' \
                '    from firm.services import base_extension as _be; _be.install()  # MUTANT M-D
    state["repair"] = (
        "founding cannot install'
all_sets green green green red green
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
