#!/usr/bin/env bash
# Cadre v0.1 end-to-end installer smoke test.
#
# Tests the real user path:
#   1. Build wheel from source
#   2. Install into a fresh venv (no PYTHONPATH tricks)
#   3. Bootstrap an empty workspace with `cadre init . --demo --install-hooks`
#   4. Verify artifacts: .firm/firm.db, hooks, settings.json
#   5. Smoke-test demo firm + gap detection
#
# Safe to re-run. Cleans /tmp/cadre-e2e* before starting.
#
# Usage:
#   bash scripts/e2e-test.sh
#
# Exit code: 0 on full pass, non-zero on first failure.

set -euo pipefail

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
else
    GREEN=''; RED=''; YELLOW=''; BLUE=''; NC=''
fi

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }
step() { echo -e "\n${BLUE}▶${NC} ${YELLOW}$1${NC}"; }

# ---------------------------------------------------------------------------
# Locate repo root
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
echo -e "${BLUE}Cadre E2E Test${NC}"
echo "Repo:       $REPO_ROOT"

VENV_DIR="/tmp/cadre-e2e-venv"
WORKSPACE="/tmp/cadre-e2e-workspace"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
step "Cleanup previous runs"
rm -rf "$VENV_DIR" "$WORKSPACE" "$REPO_ROOT/dist"
pass "cleared $VENV_DIR, $WORKSPACE, $REPO_ROOT/dist"

# ---------------------------------------------------------------------------
# 1. Build wheel
# ---------------------------------------------------------------------------
step "1. Build wheel from source"
python3 -m pip install --quiet --upgrade build 2>&1 | tail -3 || true
python3 -m build --wheel --outdir "$REPO_ROOT/dist" > /tmp/cadre-build.log 2>&1 || {
    cat /tmp/cadre-build.log
    fail "wheel build failed"
}
WHEEL="$(ls "$REPO_ROOT"/dist/cadre-*.whl | head -1)"
[ -f "$WHEEL" ] || fail "no wheel produced in dist/"
pass "built: $(basename "$WHEEL")"

# ---------------------------------------------------------------------------
# 2. Fresh venv + install
# ---------------------------------------------------------------------------
step "2. Create fresh venv and install"
python3 -m venv "$VENV_DIR"
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet "$WHEEL"
pass "installed in $VENV_DIR"

# ---------------------------------------------------------------------------
# 3. Console scripts exist
# ---------------------------------------------------------------------------
step "3. Verify console scripts"
command -v cadre >/dev/null || fail "cadre command not on PATH"
command -v firm  >/dev/null || fail "firm command not on PATH"
pass "cadre and firm both resolve"

VER="$(cadre --version)"
echo "    $VER"
[[ "$VER" == cadre* ]] || fail "expected 'cadre X.Y.Z', got: $VER"
pass "version string uses cadre"

# Python import surface
python3 -c "import firm; assert firm.__framework_name__ == 'Cadre', firm.__framework_name__" \
    && pass "firm.__framework_name__ == Cadre" \
    || fail "runtime constant wrong"

# ---------------------------------------------------------------------------
# 4. Bootstrap workspace
# ---------------------------------------------------------------------------
step "4. Bootstrap workspace: cadre init . --demo --install-hooks"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"
cadre init . --demo --install-hooks | tee /tmp/cadre-init.out
cd "$REPO_ROOT"

# Artifacts
[ -f "$WORKSPACE/.firm/firm.db" ]                               || fail "missing .firm/firm.db"
[ -f "$WORKSPACE/.claude/hooks/cadre-session-pulse.py" ]        || fail "missing session-pulse hook"
[ -f "$WORKSPACE/.claude/settings.json" ]                       || fail "missing settings.json"
pass ".firm/firm.db, session-pulse hook, settings.json all present"

# Hook registered
grep -q 'cadre-session-pulse.py' "$WORKSPACE/.claude/settings.json" \
    && pass "hook registered in settings.json" \
    || fail "hook not registered"

# Registered is not running, and two things hide the difference.
#
# The checks above pass on a hook that can never execute. Running it naively
# here would not catch that either: `source activate` above put $VENV_DIR/bin
# on PATH, so bare `python3` is the venv python and imports firm happily, and
# on a developer box an editable-install .pth puts firm on the path for every
# interpreter besides. Claude Code has neither.
#
# So the command is read back out of settings.json -- never chosen here, since
# what cadre init wired IS the thing under test -- and run with the venv off
# PATH, VIRTUAL_ENV unset and user site suppressed.
HOOK_CMD=$(python3 - "$WORKSPACE/.claude/settings.json" <<'PY' || true
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
for entry in (data.get("hooks") or {}).get("SessionStart") or []:
    for h in entry.get("hooks") or [entry]:
        cmd = h.get("command") or ""
        if "cadre-session-pulse" in cmd:
            print(cmd)
            raise SystemExit(0)
raise SystemExit(1)
PY
)
[ -n "$HOOK_CMD" ] || fail "no SessionStart command for the session-pulse hook in settings.json"

# Claude Code expands this one variable at invocation; leaving it literal would
# test a path that does not exist and blame the hook for it. Nothing else is
# expanded -- the interpreter stays exactly as registered.
HOOK_CMD=${HOOK_CMD//\$CLAUDE_PROJECT_DIR/$WORKSPACE}
HOOK_CMD=${HOOK_CMD//\$\{CLAUDE_PROJECT_DIR\}/$WORKSPACE}

SESSION_PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -vx "$VENV_DIR/bin" | paste -sd: -)
HOOK_PAYLOAD=$(printf '{"session_id": "e2e-test", "cwd": "%s"}' "$WORKSPACE")

run_hook_as_registered() {
    printf '%s' "$HOOK_PAYLOAD" | env -u VIRTUAL_ENV \
        PATH="$SESSION_PATH" PYTHONNOUSERSITE=1 FIRM_SRC= \
        sh -c "cd '$WORKSPACE' && $HOOK_CMD" 2>&1 || true
}

HOOK_OUT=$(run_hook_as_registered)
case "$HOOK_OUT" in
    *active-roster*)
        pass "the hook AS REGISTERED emits the roster outside the venv" ;;
    *)
        echo "    command: $HOOK_CMD"
        echo "    output:  ${#HOOK_OUT} bytes"
        fail "the registered hook emitted no roster outside the venv -- it exits 0 and says nothing, which its contract reads as 'no firm here'" ;;
esac

# RED ARM. The two arms differ by exactly one thing: the recorded interpreter
# path. Remove it and a firm that IS here must go quiet on stdout and LOUD on
# stderr, which is the third state the fix introduced.
#
# Not `-s`: the hook reads the recorded path and puts it on sys.path itself, so
# it serves the roster whichever interpreter invoked it. An arm built on that
# premise reports the fix as a defect.
MARKER="$WORKSPACE/.firm/python-path"
[ -f "$MARKER" ] || fail "no $MARKER recorded, so the red arm below would prove nothing"
cp "$MARKER" "$MARKER.e2e-saved"
rm -f "$MARKER"
HOOK_OUT_RED=$(run_hook_as_registered)
mv "$MARKER.e2e-saved" "$MARKER"

case "$HOOK_OUT_RED" in
    *active-roster*)
        fail "RED ARM: the hook still emitted a roster with its recorded path removed, so the check above cannot discriminate" ;;
    *"could not be imported"*)
        pass "RED ARM: with the recorded path removed the hook goes quiet and says why" ;;
    *)
        echo "    output: ${#HOOK_OUT_RED} bytes"
        fail "RED ARM: the hook emitted no roster but said nothing about why -- that silence is exactly what an empty directory looks like" ;;
esac

# ---------------------------------------------------------------------------
# 5. Demo firm structure
# ---------------------------------------------------------------------------
step "5. Demo firm structure"
python3 <<PY
import sqlite3
from pathlib import Path
from firm.core.db import connect, get_db_path
from firm.core import repo

conn = connect(get_db_path(Path('$WORKSPACE')))
firm = repo.get(conn, 'firm', 'demo')
assert firm is not None, 'demo firm not seeded'
members = repo.find(conn, 'member', firm_id='demo')
ops = repo.find(conn, 'operation', firm_id='demo')
projects = repo.find(conn, 'project', firm_id='demo')
units = repo.find(conn, 'unit', firm_id='demo')
assert len(members) == 2, f'expected 2 members, got {len(members)}'
assert len(ops) == 1
assert len(projects) == 1
assert len(units) == 1
names = {m['name'] for m in members}
assert names == {'Pen', 'Edit'}, f'unexpected member names: {names}'
unit = units[0]
assert unit['claimed_by'] is None, 'unit should be unclaimed'
print('  demo firm: Pen + Edit, 1 op, 1 project, 1 unclaimed unit')
conn.close()
PY
pass "entity counts + names correct"

# ---------------------------------------------------------------------------
# 6. Gap detection runs
# ---------------------------------------------------------------------------
step "6. Gap detection smoke test"
python3 <<PY
from pathlib import Path
from firm.core.db import connect, get_db_path
from firm.heuristics.gaps import detect_gaps

conn = connect(get_db_path(Path('$WORKSPACE')))
report = detect_gaps(conn, 'demo')
assert len(report['unclaimed_units']) == 1, f'expected 1 unclaimed, got {len(report["unclaimed_units"])}'
print(f'  summary: {report["summary"]}')
conn.close()
PY
pass "detect_gaps surfaces the demo unclaimed unit"

# ---------------------------------------------------------------------------
# 7. Idempotence
# ---------------------------------------------------------------------------
step "7. Re-run idempotence"
cd "$WORKSPACE"
cadre init . --demo --install-hooks > /tmp/cadre-init2.out 2>&1
cd "$REPO_ROOT"
grep -q 'already' /tmp/cadre-init2.out \
    && pass "second run reports 'already installed'" \
    || fail "idempotence broken on re-run"

python3 <<PY
from pathlib import Path
from firm.core.db import connect, get_db_path
from firm.core import repo
conn = connect(get_db_path(Path('$WORKSPACE')))
members = repo.find(conn, 'member', firm_id='demo')
assert len(members) == 2, f'members duplicated: {len(members)}'
conn.close()
PY
pass "no duplicate entities after re-run"

# ---------------------------------------------------------------------------
# 8. MCP tool surface imports cleanly
# ---------------------------------------------------------------------------
step "8. MCP server surface (import + tool count)"
python3 <<PY
from firm.mcp.tools import mcp
# 37, measured 2026-09-10 three independent ways on the same tree: the runtime
# registry in a wheel installed into a fresh venv, the runtime registry in the
# source checkout, and a count of @mcp.tool decorators in
# src/firm/mcp/tools.py. It read 33 until today and the number had drifted.
#
# This asserts the surface has not changed by accident. It is EXPECTED to fail
# when the MCP-to-CLI migration retires tools (docs/MCP-TO-CLI-MIGRATION.md,
# phases 3 to 5) -- when it does, re-count and move the number, do not delete
# the check.
EXPECTED = 37
count = len(mcp._tool_manager._tools)
assert count == EXPECTED, f'expected {EXPECTED} MCP tools, got {count}'
print(f'  {count} MCP tools registered')
PY
pass "MCP surface intact (37 tools)"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
deactivate
echo ""
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  ALL E2E CHECKS PASSED${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo ""
echo "Workspace:  $WORKSPACE"
echo "Venv:       $VENV_DIR"
echo ""
echo "To explore manually:"
echo "  source $VENV_DIR/bin/activate"
echo "  cd $WORKSPACE"
echo "  # inspect .firm/firm.db, .claude/hooks/, .claude/settings.json"
echo ""
echo "To test in Claude Code:"
echo "  Open $WORKSPACE in Claude Code. Session start should inject"
echo "  <active-roster> with Pen (Writer) + Edit (Editor) from the demo firm."
