#!/usr/bin/env bash
# godwit - render, validate and install the real manifest with the real base,
# under an isolated BASE_HOME. The operator's own extensions directory is a
# control: it must be byte-identical afterwards.
set -u
cd /home/chriskahler/dev/cadre-wt-extension || exit 90

R="/tmp/godwit-extinstall-$$"
mkdir -p "$R"
REAL=/home/chriskahler/.base-gbl/extensions/cadre.toml
BEFORE=$(md5sum "$REAL" | cut -d' ' -f1)

echo "PROVENANCE base=$(command -v base) $(base --version) md5=$(md5sum "$(command -v base)" | cut -d' ' -f1)"
echo "PROVENANCE manifest md5=$(md5sum src/firm/base_ext/cadre.toml | cut -d' ' -f1)"
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
AFTER=$(md5sum "$REAL" | cut -d' ' -f1)
echo "  before $BEFORE"
echo "  after  $AFTER"
if [ "$BEFORE" = "$AFTER" ]; then echo "  UNTOUCHED"; else echo "  CHANGED — STOP"; exit 1; fi
echo "=== END ==="
