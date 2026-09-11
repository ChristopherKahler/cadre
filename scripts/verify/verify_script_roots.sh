#!/bin/bash
# Issue #64 proof. Every refusal must be SEEN firing, and the happy path must
# still work. A refuse code nobody has watched trigger is a comment.
set -u
# Derived, not hard-coded. This script is the PROOF for issue #64 and it
# shipped carrying the very defect it proves fixed -- caught by
# tests/test_no_hardcoded_roots.py, which is the point of having the guard
# run in CI rather than trusting a reading.
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd) || exit 90
cd "$REPO" || exit 9
T=/tmp/godwit-i64
rm -rf "$T"; mkdir -p "$T"
fails=0

row() {  # $1 PASS|FAIL  $2 label  $3 detail
  printf '  %-6s %s\n' "$1" "$2"
  [ -n "${3:-}" ] && printf '         %s\n' "$3"
  [ "$1" = "FAIL" ] && fails=$((fails + 1))
  return 0
}
expect_rc() {  # $1 want  $2 got  $3 label  $4 detail
  if [ "$2" -eq "$1" ]; then row PASS "$3" "exit $2 — $4"
  else row FAIL "$3" "exit $2, wanted $1 — $4"; fi
}

echo "=== THE ROOT NOW COMES FROM THE SCRIPT, NOT A HARD-CODED PATH ==="
# Copy the script to a DIFFERENT tree at the same depth and check it names that
# tree, not this one. This is the whole defect: run from elsewhere, it used to
# cd here and print real hashes about files the caller never chose.
OTHER="$T/other-checkout"
mkdir -p "$OTHER/scripts/verify" "$OTHER/src/firm/base_ext"
git -C "$OTHER" init -q 2>/dev/null
printf 'name = "decoy"\n' > "$OTHER/src/firm/base_ext/decoy.toml"
cp scripts/verify/verify_ext_install.sh "$OTHER/scripts/verify/"
# head -20, not -6: the PROVENANCE manifest line sits below the sixth line, so
# the probe was reading fewer lines than its own claim needed.
out=$(cd "$OTHER" && timeout 120 bash scripts/verify/verify_ext_install.sh 2>&1 | head -20)
if printf '%s' "$out" | grep -q "REPO $OTHER"; then
  row PASS "run from another checkout, it names THAT checkout" \
      "$(printf '%s' "$out" | grep '^REPO' | head -1)"
else
  row FAIL "run from another checkout, it names THAT checkout" \
      "$(printf '%s' "$out" | head -2)"
fi
if printf '%s' "$out" | grep -q "decoy.toml"; then
  row PASS "it hashes the OTHER tree's manifest, not this one's" \
      "$(printf '%s' "$out" | grep 'PROVENANCE manifest' | head -1)"
else
  row FAIL "it hashes the OTHER tree's manifest, not this one's" \
      "$(printf '%s' "$out" | grep PROVENANCE | head -2)"
fi
if printf '%s' "$out" | grep -q "$REPO"; then
  row FAIL "it no longer reaches into the old hard-coded worktree" \
      "output still mentions $REPO"
else
  row PASS "it no longer reaches into the old hard-coded worktree" ""
fi

echo
echo "=== EXIT 91 — a root that is not a git work tree is REFUSED ==="
NOGIT="$T/not-a-repo"
mkdir -p "$NOGIT/scripts/verify" "$NOGIT/src/firm/base_ext"
printf 'x\n' > "$NOGIT/src/firm/base_ext/m.toml"
cp scripts/verify/verify_ext_install.sh "$NOGIT/scripts/verify/"
out=$(cd "$NOGIT" && timeout 120 bash scripts/verify/verify_ext_install.sh 2>&1); rc=$?
expect_rc 91 "$rc" "verify_ext_install.sh refuses a non-git root" \
          "$(printf '%s' "$out" | head -1)"

echo
echo "=== EXIT 92 — a repo with no manifest is REFUSED ==="
NOMAN="$T/no-manifest"
mkdir -p "$NOMAN/scripts/verify" "$NOMAN/src/firm/base_ext"
git -C "$NOMAN" init -q 2>/dev/null
cp scripts/verify/verify_ext_install.sh "$NOMAN/scripts/verify/"
out=$(cd "$NOMAN" && timeout 120 bash scripts/verify/verify_ext_install.sh 2>&1); rc=$?
expect_rc 92 "$rc" "verify_ext_install.sh refuses a repo with an empty base_ext" \
          "$(printf '%s' "$out" | grep REFUSE | head -1)"

echo
echo "=== EXIT 93 — the control's RED ARM must itself be able to fail ==="
BROKE="$T/broken-state"
mkdir -p "$BROKE/scripts/verify" "$BROKE/src/firm/base_ext"
git -C "$BROKE" init -q 2>/dev/null
printf 'x\n' > "$BROKE/src/firm/base_ext/m.toml"
# Cripple state() so it returns a constant. Everything else is untouched, so if
# this still reports UNTOUCHED the red arm proves nothing.
sed 's|if \[ -f "\$1" \]; then md5sum "\$1" | cut -d. -f1 | head -0; true' /dev/null 2>/dev/null
python3 - "$REPO/scripts/verify/verify_ext_install.sh" "$BROKE/scripts/verify/verify_ext_install.sh" <<'PYEOF'
import sys, pathlib
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
t = src.read_text(encoding="utf-8")
old = '''state() {
  if [ -f "$1" ]; then md5sum "$1" | cut -d' ' -f1; else echo MISSING; fi
}'''
assert t.count(old) == 1, f"state() anchor found {t.count(old)} times"
dst.write_text(t.replace(old, 'state() {\n  echo CONSTANT\n}'), encoding="utf-8")
print("  (state() crippled to a constant in the copy)")
PYEOF
out=$(cd "$BROKE" && timeout 300 bash scripts/verify/verify_ext_install.sh 2>&1); rc=$?
expect_rc 93 "$rc" "a state() that cannot discriminate is caught by its red arm" \
          "$(printf '%s' "$out" | grep 'RED ARM' | head -1)"

echo
echo "=== THE CONTROL NO LONGER PASSES ON A FILE THAT DOES NOT EXIST ==="
# The original defect: md5sum on an absent path prints nothing, two empty
# strings compare equal, and the run reports UNTOUCHED. MISSING must not be
# confusable with a hash, and an absent file that gets CREATED must read as a
# change.
# $REPO, passed as argv. The heredoc is quoted so the shell does not expand
# inside it, which is why this reads the path from sys.argv rather than
# interpolating. It used to open the worktree by absolute path -- the prover for
# issue #64 containing issue #64. It passed only because that worktree happened
# to sit on this branch, so the file was byte-identical: a coincidence of machine
# state, not a property of the code. From any other checkout it either dies on a
# missing path or, worse, proves ANOTHER branch's copy while reporting PASS.
python3 - "$REPO" <<'PYEOF'
import subprocess
import sys
script = f"{sys.argv[1]}/scripts/verify/verify_ext_install.sh"
body = open(script, encoding="utf-8").read()
start = body.index("state() {")
end = body.index("}", start) + 1
fn = body[start:end]
probe = "/tmp/godwit-i64/absent-probe.toml"
sh = f"""
{fn}
rm -f {probe}
A=$(state {probe})
printf 'created' > {probe}
B=$(state {probe})
rm -f {probe}
C=$(state {probe})
echo "absent=$A created=$B absent_again=$C"
[ "$A" = MISSING ] || exit 1
[ "$B" = MISSING ] && exit 2
[ "$A" = "$B" ] && exit 3
[ "$C" = MISSING ] || exit 4
exit 0
"""
p = subprocess.run(["bash", "-c", sh], capture_output=True, text=True, timeout=60)
print("  " + p.stdout.strip())
codes = {0: None, 1: "an absent file did not read MISSING",
         2: "a created file read MISSING", 3: "absent and created compared EQUAL",
         4: "a deleted file did not read MISSING"}
if p.returncode == 0:
    print("  PASS   absent reads MISSING, creating it moves the state, and the two never match")
else:
    print(f"  FAIL   {codes.get(p.returncode, p.returncode)}")
    raise SystemExit(1)
PYEOF
[ $? -ne 0 ] && fails=$((fails + 1))

echo
echo "=== accept_relay_title.py — same root, same refusals ==="
PYOTHER="$T/py-not-git"
mkdir -p "$PYOTHER/scripts/verify" "$PYOTHER/src/firm"
cp scripts/verify/accept_relay_title.py "$PYOTHER/scripts/verify/"
out=$(cd "$PYOTHER" && timeout 120 "$REPO/.venv/bin/python" scripts/verify/accept_relay_title.py 2>&1); rc=$?
expect_rc 91 "$rc" "accept_relay_title.py refuses a non-git root" \
          "$(printf '%s' "$out" | grep REFUSE | head -1)"

PYNOSRC="$T/py-no-src"
mkdir -p "$PYNOSRC/scripts/verify"
git -C "$PYNOSRC" init -q 2>/dev/null
cp scripts/verify/accept_relay_title.py "$PYNOSRC/scripts/verify/"
out=$(cd "$PYNOSRC" && timeout 120 "$REPO/.venv/bin/python" scripts/verify/accept_relay_title.py 2>&1); rc=$?
expect_rc 92 "$rc" "accept_relay_title.py refuses a repo with no src/firm" \
          "$(printf '%s' "$out" | grep REFUSE | head -1)"

PYNOBASE="$T/py-no-base"
mkdir -p "$PYNOBASE"
# `timeout` is itself resolved through PATH, so emptying PATH in front of it
# returned 127 and the probe never reached python at all. Resolve it first and
# let only the CHILD lose its PATH.
TIMEOUT=$(command -v timeout)
out=$(cd "$REPO" && "$TIMEOUT" 120 env PATH=/nonexistent-dir "$REPO/.venv/bin/python" \
      scripts/verify/accept_relay_title.py 2>&1); rc=$?
expect_rc 93 "$rc" "accept_relay_title.py refuses when base is not on PATH" \
          "$(printf '%s' "$out" | grep REFUSE | head -1)"

echo
echo "=== THE HAPPY PATH STILL WORKS IN THE REAL REPO ==="
out=$(timeout 600 bash scripts/verify/verify_ext_install.sh 2>&1); rc=$?
printf '%s\n' "$out" | grep -E "^REPO|PROVENANCE manifest|CONTROL watching|UNTOUCHED|CHANGED|red arm|END" | sed 's/^/  /'
expect_rc 0 "$rc" "verify_ext_install.sh passes in its own repo" "rc=$rc"

echo
if [ "$fails" -ne 0 ]; then echo "ISSUE 64 PROOF FAILED: $fails"; exit 1; fi
echo "ISSUE 64 PROOF PASSED: every refusal seen firing, happy path still green"
