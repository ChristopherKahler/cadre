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
count = len(mcp._tool_manager._tools)
assert count == 37, f'expected 37 MCP tools, got {count}'
print(f'  37 MCP tools registered')
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
