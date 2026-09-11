#!/bin/bash
# F6 — prove by EXEC that the two install routes agree.
#
# THE DEFECT. There are two ways to install Cadre's base extension manifest:
#
#   ROUTE A  `cadre extension install` — renders the machine-specific
#            placeholders, validates, installs. `base cadre` then runs.
#   ROUTE B  `base extension install <site-packages>/firm/base_ext/cadre.toml`
#            — the obvious thing to do with a file the package ships. It used
#            to return 0, print "Installed cadre v0.2.0", list the extension,
#            and leave `base cadre` exiting 127.
#
# Either answer is acceptable as long as the user is TOLD which one they got.
# An install that reports success over a dead command is not.
#
# THE FIX THIS PROVES. Measured on base 0.15.0: `base extension validate` and
# `base extension install` BOTH return 0 for a manifest whose handler points at
# a path that exists nowhere — base never looks at the handler. So no content
# in the manifest can make route B refuse. base DOES refuse a file it cannot
# read (rc 1, "Cannot read file"). The shipped file is therefore named
# cadre.toml.template and no cadre.toml exists in the package, which turns the
# route-B command into a refusal at install time.
#
# Run it:  bash scripts/verify/verify_f6_refuse.sh
# Needs:   a built wheel in dist (scripts/verify/verify_packaging.py
#          makes one), python3, and a `base` on PATH built for this platform.
set -u
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
# The base under test, overridable: BASE=/path/to/base verify_f6_refuse.sh
BASE=${BASE:-$(command -v base)}
ROOT=/tmp/godwit-f6-exec

# ---- ASSERT the base under test is THIS platform's --------------------------
# A Windows base runs happily under WSL interop, ignores a POSIX BASE_HOME, and
# writes the operator's real tier while every assertion below still passes.
kind=$(head -c 4 "$BASE" | od -An -c | tr -d ' ')
case "$kind" in
  *177ELF*) echo "base under test: $BASE (ELF, $($BASE --version))" ;;
  *) echo "ABORT: $BASE is not an ELF binary (got '$kind')"; exit 9 ;;
esac

rm -rf "$ROOT"; mkdir -p "$ROOT"
cd "$ROOT" || exit 9

fails=0
row() {  # $1 = PASS|FAIL, $2 = name, $3 = detail
  printf '  %-6s %s\n' "$1" "$2"
  [ -n "${3:-}" ] && printf '         %s\n' "$3"
  [ "$1" = "FAIL" ] && fails=$((fails + 1))
  return 0
}

WHL=$(ls -1 "$REPO"/dist/cadre-*.whl 2>/dev/null | tail -1)
[ -f "$WHL" ] || { echo "ABORT: no wheel at $REPO/dist"; exit 9; }
echo "wheel under test: $WHL"
echo

python3 -m venv "$ROOT/venv" >/dev/null 2>&1
PY="$ROOT/venv/bin/python"
"$PY" -m pip install -q "$WHL" 2>&1 | tail -2
CADRE="$ROOT/venv/bin/cadre"
[ -x "$CADRE" ] || { echo "ABORT: no cadre console script in the venv"; exit 9; }

SP=$("$PY" -I -c "import firm,pathlib;print(pathlib.Path(firm.__file__).parent)")
echo "installed package: $SP"
echo

echo "=== THE SHIPPED FILES ==="
ls -1 "$SP/base_ext/"
if [ -f "$SP/base_ext/cadre.toml" ]; then
  row FAIL "the installed package ships no cadre.toml" "$SP/base_ext/cadre.toml exists — F6 is reopened"
else
  row PASS "the installed package ships no cadre.toml" "only cadre.toml.template is there"
fi
echo

# ---- ROUTE A: the product's own installer -----------------------------------
echo "=== ROUTE A — cadre extension install ==="
export BASE_HOME="$ROOT/homeA"
mkdir -p "$BASE_HOME" "$ROOT/wsA"
cd "$ROOT/wsA" || exit 9

"$BASE" cadre --version > "$ROOT/a0.out" 2>&1; rc=$?
[ "$rc" -ne 0 ] && row PASS "NEGATIVE CONTROL: route A home has no cadre command yet" "rc=$rc" \
                || row FAIL "NEGATIVE CONTROL: route A home has no cadre command yet" "rc=0 before installing anything"

"$CADRE" extension install > "$ROOT/a1.out" 2>&1; rcA=$?
[ "$rcA" -eq 0 ] && row PASS "ROUTE A: cadre extension install reports success" "rc=$rcA" \
                 || row FAIL "ROUTE A: cadre extension install reports success" "rc=$rcA :: $(tail -3 "$ROOT/a1.out")"

MAN="$BASE_HOME/.base-gbl/extensions/cadre.toml"
# Comment lines are excluded on purpose. This manifest's header QUOTES
# {{total}} while documenting base issue #6, and a placeholder sitting in a
# comment cannot break a command -- only one in a value can. Scanning the whole
# file reports the documentation as a defect.
if [ -f "$MAN" ] && ! grep -v '^[[:space:]]*#' "$MAN" | grep -q '{{'; then
  row PASS "ROUTE A: the installed manifest has no unrendered placeholders in VALUES" "$MAN"
else
  row FAIL "ROUTE A: the installed manifest has no unrendered placeholders in VALUES" \
           "$(grep -v '^[[:space:]]*#' "$MAN" 2>/dev/null | grep -o '{{[^}]*}}' | sort -u | tr '\n' ' ')"
fi

"$BASE" cadre --version > "$ROOT/a2.out" 2>&1; rcA2=$?
if [ "$rcA2" -eq 0 ]; then
  row PASS "ROUTE A: 'base cadre --version' EXECUTES" "rc=0 :: $(head -1 "$ROOT/a2.out")"
else
  row FAIL "ROUTE A: 'base cadre --version' EXECUTES" "rc=$rcA2 :: $(head -2 "$ROOT/a2.out")"
fi
echo

# ---- ROUTE B: the obvious hand install, which F6 is about -------------------
echo "=== ROUTE B — base extension install <the path a user would type> ==="
export BASE_HOME="$ROOT/homeB"
mkdir -p "$BASE_HOME" "$ROOT/wsB"
cd "$ROOT/wsB" || exit 9

"$BASE" cadre --version > "$ROOT/b0.out" 2>&1; rc=$?
[ "$rc" -ne 0 ] && row PASS "NEGATIVE CONTROL: route B home has no cadre command yet" "rc=$rc" \
                || row FAIL "NEGATIVE CONTROL: route B home has no cadre command yet" "rc=0 before installing anything"

"$BASE" extension install "$SP/base_ext/cadre.toml" > "$ROOT/b1.out" 2>&1; rcB=$?
if [ "$rcB" -ne 0 ]; then
  row PASS "THE F6 ROW: the hand install REFUSES at install time" \
           "rc=$rcB :: $(head -1 "$ROOT/b1.out")"
else
  "$BASE" cadre --version > "$ROOT/b2.out" 2>&1; rcB2=$?
  if [ "$rcB2" -eq 0 ]; then
    row PASS "THE F6 ROW: the hand install succeeded AND the command runs" "rc=0"
  else
    row FAIL "THE F6 ROW: install rc=0 and 'base cadre' is DEAD — THIS IS F6" \
             "cadre rc=$rcB2 :: $(head -2 "$ROOT/b2.out")"
  fi
fi
echo

# ---- ROUTE B' : whoever types the .template path anyway ---------------------
echo "=== ROUTE B' — installing the TEMPLATE explicitly ==="
export BASE_HOME="$ROOT/homeC"
mkdir -p "$BASE_HOME" "$ROOT/wsC"
cd "$ROOT/wsC" || exit 9

"$BASE" extension install "$SP/base_ext/cadre.toml.template" > "$ROOT/c1.out" 2>&1; rcC=$?
echo "  install rc=$rcC :: $(head -1 "$ROOT/c1.out")"
"$BASE" cadre --version > "$ROOT/c2.out" 2>&1; rcC2=$?
echo "  base cadre rc=$rcC2"
sed -n '1,3p' "$ROOT/c2.out"
if [ "$rcC2" -eq 0 ]; then
  row PASS "ROUTE B': the template install happens to work" "rc=0"
elif grep -q 'UNRENDERED-run-cadre-extension-install-instead' "$ROOT/c2.out"; then
  row PASS "ROUTE B': the failure NAMES ITS OWN REMEDY in base's own output" \
           "the error carries 'run-cadre-extension-install-instead'"
else
  row FAIL "ROUTE B': the failure is a mystery path, not an instruction" \
           "$(head -2 "$ROOT/c2.out")"
fi
echo

# ---- RED ARM: the F6 row must be able to go red -----------------------------
# Put a cadre.toml back exactly where the old package shipped one and re-run
# route B. If the row still reads PASS, it is not measuring anything.
echo "=== RED ARM — restore the old shape and the F6 row must FAIL ==="
sed -e 's/{{handler-UNRENDERED-run-cadre-extension-install-instead}}/no-such-handler/' \
    -e "s|{{framework_dir}}|$ROOT/wsD|" \
    "$SP/base_ext/cadre.toml.template" > "$SP/base_ext/cadre.toml"
export BASE_HOME="$ROOT/homeD"
mkdir -p "$BASE_HOME" "$ROOT/wsD"
cd "$ROOT/wsD" || exit 9
"$BASE" extension install "$SP/base_ext/cadre.toml" > "$ROOT/d1.out" 2>&1; rcD=$?
"$BASE" cadre --version > "$ROOT/d2.out" 2>&1; rcD2=$?
if [ "$rcD" -eq 0 ] && [ "$rcD2" -ne 0 ]; then
  row PASS "RED ARM: with cadre.toml restored, the F6 defect reappears" \
           "install rc=0, base cadre rc=$rcD2 — so the row above measures the rename, not luck"
else
  row FAIL "RED ARM: could not reproduce F6 with cadre.toml restored" \
           "install rc=$rcD, cadre rc=$rcD2 — the F6 row cannot be trusted"
fi
rm -f "$SP/base_ext/cadre.toml"
echo

# Leave nothing of ours in the operator's tier: every BASE_HOME above was under
# $ROOT, and $ROOT goes away with the next run.
echo "============================================================"
if [ "$fails" -ne 0 ]; then
  echo "F6 EXEC PROOF FAILED: $fails row(s)"
  exit 1
fi
echo "F6 EXEC PROOF PASSED: every row measured by running a command"
