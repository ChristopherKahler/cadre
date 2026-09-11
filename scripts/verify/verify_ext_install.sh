#!/usr/bin/env bash
# Render, validate and install the real manifest with the real base, under an
# isolated BASE_HOME. The operator's own extensions directory is the control:
# it must be untouched afterwards.
#
# THREE THINGS THIS USED TO GET WRONG, all the same shape -- a check that
# reports a real-looking result about something other than what it names.
#
# 1. It `cd`-ed into a hard-coded worktree path. Run from any other checkout it
#    silently measured THAT tree instead, and printed provenance hashes for
#    files the caller never chose. A real number from the wrong file is worse
#    than no number, because nothing about the output says it is wrong.
#    The root now comes from this script's own location, the same way
#    scripts/check-action-pins.py does it, and a root that is not a git work
#    tree is REFUSED rather than measured.
#
# 2. The manifest hash named `base_ext/cadre.toml`, which F6 renamed to
#    `cadre.toml.template`. `md5sum` then wrote an error to stderr and an EMPTY
#    string into the PROVENANCE line -- a missing hash reading as a blank, on
#    the line whose whole job is saying which bytes were tested. It now hashes
#    every file actually in base_ext and refuses if there are none, so the next
#    rename cannot quietly empty it.
#
# 3. THE CONTROL PASSED ON A FILE THAT DOES NOT EXIST. `md5sum` on a missing
#    path prints nothing, so BEFORE and AFTER were both the empty string, they
#    compared equal, and the run reported UNTOUCHED -- in the one row that
#    exists to prove the operator's own tier was not written. Absent is a
#    passing value. The state function below returns the literal MISSING for an
#    absent file, so absent-then-created reads as CHANGED instead of as a
#    match, and the watched path and its state are printed rather than trusted.
#
# Exit codes are distinct so a caller can tell refusals apart:
#   90 the repo root could not be resolved from this script's location
#   91 that root is not a git work tree
#   92 src/firm/base_ext carries no manifest, so provenance cannot be stated
#   93 the control cannot discriminate (its red arm did not go red)
#    1 the control fired: the operator's extensions directory was written
set -u

# The repo, from THIS file's location. scripts/verify/x.sh -> ../..
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd) || {
  echo "REFUSE: cannot resolve the repo root from ${BASH_SOURCE[0]}"; exit 90; }
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "REFUSE: $REPO is not a git work tree, so this would measure files that"
  echo "        are not the checkout under test"; exit 91; }
cd "$REPO" || exit 90
echo "REPO $REPO  ($(git -C "$REPO" rev-parse --short=7 HEAD) on $(git -C "$REPO" rev-parse --abbrev-ref HEAD))"

R="/tmp/cadre-extinstall-$$"
mkdir -p "$R"
REAL=${CADRE_OPERATOR_EXT:-$HOME/.base-gbl/extensions/cadre.toml}

# MISSING, never the empty string. `md5sum` on an absent path prints nothing,
# and two empty strings compare equal -- which is how the control below passed
# on a file that was not there.
state() {
  if [ -f "$1" ]; then md5sum "$1" | cut -d' ' -f1; else echo MISSING; fi
}

BEFORE=$(state "$REAL")

MANIFESTS=$(find src/firm/base_ext -maxdepth 1 -type f 2>/dev/null | sort)
[ -n "$MANIFESTS" ] || {
  echo "REFUSE: src/firm/base_ext holds no manifest under $REPO, so this run"
  echo "        cannot say which bytes it tested"; exit 92; }

echo "PROVENANCE base=$(command -v base) $(base --version) md5=$(md5sum "$(command -v base)" | cut -d' ' -f1)"
for m in $MANIFESTS; do
  echo "PROVENANCE manifest $m md5=$(md5sum "$m" | cut -d' ' -f1)"
done
echo "CONTROL watching $REAL  (state before: $BEFORE)"
echo "isolated BASE_HOME = $R"
echo

echo "=== install() : render -> validate -> install -> read back ==="
BASE_HOME="$R" env -u WT_SESSION .venv/bin/python - <<'PY'
from firm.services import base_extension as be
res = be.install()
for k in ("ok", "validated", "installed", "read_back", "path", "reason"):
    print(f"  {k} = {res[k]}")
PY
echo

L="$R/.base-gbl/extensions/cadre.toml"
echo "=== the landed file, read from disk ==="
if [ -f "$L" ]; then
  echo "  exists yes, bytes $(wc -c < "$L")"
  echo "  unfilled placeholders: $(grep -c 'framework_dir}}' "$L")"
  echo "  $(grep '^framework_dir' "$L")"
  echo "  $(grep '^version' "$L")"
else
  echo "  exists NO"
fi
echo

echo "=== base's own view under the isolated home ==="
BASE_HOME="$R" base extension list 2>&1 | sed -n '1,5p' | sed 's/^/  /'
echo

echo "=== does the installed manifest actually FIRE? ==="
mkdir -p "$R/ws/.firm/base-export"
printf '[{"id":"MEM-001","name":"Vantage","role":"Chief of Staff","firm_id":"zqfirm"}]' \
  > "$R/ws/.firm/base-export/members.json"
printf '[{"id":"UNIT-001","name":"Ship it","status":"open","firm_id":"zqfirm"}]' \
  > "$R/ws/.firm/base-export/units.json"
printf '[]' > "$R/ws/.firm/base-export/gates.json"
( cd "$R/ws" && BASE_HOME="$R" base scaffold . >/dev/null 2>&1 )

printf '{"session_id":"gwext-%s","transcript_path":"/tmp/n.jsonl","cwd":"%s","hook_event_name":"SessionStart","source":"startup"}' "$$" "$R/ws" \
  | ( cd "$R/ws" && env -u WT_SESSION BASE_HOME="$R" base hook session-start ) >"$R/o.ss" 2>"$R/e.ss"
echo "  session-start signpost seen: $(grep -c 'base cadre brief' "$R/o.ss")"
echo "  ingest line on stderr:       $(grep -o 'ingested [0-9]* entities from [0-9]* file' "$R/e.ss" | head -1)"
echo "  CadreMember in graph:        $(grep -c CadreMember "$R/ws/.base/graph.nq" 2>/dev/null)"
echo "  CadreUnit in graph:          $(grep -c CadreUnit "$R/ws/.base/graph.nq" 2>/dev/null)"
echo "  a name never used:           $(grep -c zqnevernever "$R/ws/.base/graph.nq" 2>/dev/null)"

printf '{"session_id":"gwext2-%s","transcript_path":"/tmp/n.jsonl","cwd":"%s","hook_event_name":"UserPromptSubmit","prompt":"member run starting"}' "$$" "$R/ws" \
  | ( cd "$R/ws" && env -u WT_SESSION BASE_HOME="$R" base hook user-prompt-submit ) >"$R/o.up" 2>&1
echo "  prompt domain fired:         $(grep -c 'ext:cadre:cadre-firm' "$R/o.up")"
echo "  CLI verb taught:             $(grep -c 'firm unit create' "$R/o.up")"
echo "  MCP name taught:             $(grep -c 'unit_create' "$R/o.up")"
echo

echo "=== CONTROL: the operator's own extensions dir must be untouched ==="
AFTER=$(state "$REAL")
echo "  watching $REAL"
echo "  before $BEFORE"
echo "  after  $AFTER"

# RED ARM, because a control that has never been seen fire is not a control.
# MISSING-to-MISSING is a legitimate pass on a machine where Cadre was never
# installed into base -- the run creating that file would still show up, since
# a created file hashes and MISSING does not. But "it did not move" proves
# nothing unless something CAN move it, so prove that here rather than assume
# it: write a probe, read its state, change it, read again.
PROBE="$R/control-probe.toml"
printf 'before' > "$PROBE"; P1=$(state "$PROBE")
printf 'after'  > "$PROBE"; P2=$(state "$PROBE")
rm -f "$PROBE";             P3=$(state "$PROBE")
if [ "$P1" = "$P2" ] || [ "$P3" != "MISSING" ]; then
  echo "  RED ARM FAILED: state() gave $P1 -> $P2 -> $P3; it cannot tell a"
  echo "  changed file from an unchanged one, or an absent file from a present"
  echo "  one, so every UNTOUCHED this script reports is meaningless"
  exit 93
fi
echo "  red arm: a changed byte moves the state, and an absent file reads MISSING"

if [ "$BEFORE" = "$AFTER" ]; then echo "  UNTOUCHED"; else echo "  CHANGED — STOP"; exit 1; fi
echo "=== END ==="
