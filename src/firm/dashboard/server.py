"""Cadre Boardroom dashboard server.

A firm-agnostic local web command center over ``.firm/firm.db``. Read layer
is one comprehensive ``/api/state`` payload (the UI polls it); write layer is
the Board's decision surface: gate approve/reject, escalation acknowledge/
resolve, goal metric refresh. Everything routes through the same service
functions Members use, so records/audit behavior is identical.

Stdlib only (ThreadingHTTPServer) — no runtime dependencies, works on any
firm workspace: ``cadre dashboard --workspace ~/firms/whatever``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from firm.core import repo
from firm.core.db import connect, db_is_remote, get_db_path, resolve_firm_id
from firm.core.migrate import apply_migrations
from firm.pulse.orchestrator import (
    _REAP_GRACE_SEC,
    _contract_timeout_sec,
    compute_load,
)
from firm.services import comment as comment_svc
from firm.services import document as document_svc
from firm.services import escalation as escalation_svc
from firm.services import gate as gate_svc
from firm.services import goal as goal_svc
from firm.services import member as member_svc
from firm.services import run as run_svc
from firm.services import unit as unit_svc
from firm.services._records import log_event
from firm.secrets.vault import VaultError
from firm.sysconfig import service as sysconfig_svc

_INDEX_HTML = Path(__file__).parent / "index.html"

# The Boardroom rebuild — onboarding + portfolio, served beside the current
# landing page so the surface Chris governs from daily never moves. Swap by
# flipping `/` to this once it has earned it.
_NEXT_HTML = Path(__file__).parent / "next" / "index.html"

_VIEW_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


# ---------------------------------------------------------------------------
# Firm-supplied custom views (.firm/dashboard/views.json)
# ---------------------------------------------------------------------------

def load_custom_views(workspace: Path) -> list[dict[str, Any]]:
    """Parse the firm's custom-view manifest, if any.

    Manifest shape::

        {"views": [{"id": "table", "title": "The Table",
                    "fragment": "dashboard/views/table.html",
                    "files": {"game_state": "game/game_state.json"}}]}

    ``fragment`` and every ``files`` value are resolved relative to
    ``<workspace>/.firm/`` — a firm can only expose its own state, never
    arbitrary paths. Malformed manifests degrade to no custom views;
    individual bad entries are skipped.
    """
    manifest = workspace / ".firm" / "dashboard" / "views.json"
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    views = []
    for v in data.get("views", []):
        if not isinstance(v, dict):
            continue
        vid = str(v.get("id") or "")
        if not _VIEW_ID_RE.fullmatch(vid) or not v.get("fragment"):
            continue
        files = v.get("files") or {}
        if not isinstance(files, dict):
            files = {}
        dirs = v.get("dirs") or {}
        if not isinstance(dirs, dict):
            dirs = {}
        actions = v.get("actions") or {}
        if not isinstance(actions, dict):
            actions = {}
        queries = v.get("queries") or {}
        if not isinstance(queries, dict):
            queries = {}
        mode = "fullscreen" if str(v.get("mode") or "").lower() == "fullscreen" else "embed"
        views.append({
            "id": vid,
            "title": str(v.get("title") or vid),
            "fragment": str(v["fragment"]),
            "mode": mode,
            "files": {str(k): str(p) for k, p in files.items()},
            "dirs": {str(k): str(p) for k, p in dirs.items()},
            "actions": {str(k): a for k, a in actions.items() if isinstance(a, dict)},
            "queries": {str(k): str(q) for k, q in queries.items()},
        })
    return views


def load_custom_blocks(workspace: Path) -> list[dict[str, Any]]:
    """Extension-contributed dashboard BLOCKS (the `blocks` array in views.json).

    A block renders as a card appended to the bottom of a native page (its
    ``mount``, default ``dashboard``) — the seam extensions like squad use to
    consolidate their reporting into the firm's own dashboard. Same fragment
    trust boundary as custom views (resolved inside ``.firm/``).
    """
    manifest = workspace / ".firm" / "dashboard" / "views.json"
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    blocks = []
    for b in data.get("blocks", []):
        if not isinstance(b, dict):
            continue
        bid = str(b.get("id") or "")
        if not _VIEW_ID_RE.fullmatch(bid) or not b.get("fragment"):
            continue
        mount = str(b.get("mount") or "dashboard")
        if not _VIEW_ID_RE.fullmatch(mount):
            mount = "dashboard"
        blocks.append({
            "id": bid,
            "title": str(b.get("title") or bid),
            "fragment": str(b["fragment"]),
            "mount": mount,
        })
    return blocks


def install_extension(
    workspace: Path, package: dict[str, Any], confirmed: bool,
) -> dict[str, Any]:
    """Install a drop-in extension package into this firm.

    A package is a self-contained JSON object: {id, title, mode, fragment (inline
    HTML), actions, requires[], install{cmd,description}}. Installing writes the
    fragment + merges the view into ``views.json`` (so the tab auto-appears) and,
    ONLY when *confirmed*, runs the package's declared install cmd (argv, no shell)
    to make the tool's commands available. Uploaded code is executed only behind
    that operator confirm — the whole point of the gate.
    """
    if not isinstance(package, dict):
        raise ValueError("package must be a JSON object")
    vid = str(package.get("id") or "")
    if not _VIEW_ID_RE.fullmatch(vid):
        raise ValueError("package 'id' must be a short slug [a-z0-9-]")
    title = str(package.get("title") or vid)
    mode = "fullscreen" if str(package.get("mode") or "").lower() == "fullscreen" else "embed"
    fragment = package.get("fragment")
    if not isinstance(fragment, str) or not fragment.strip():
        raise ValueError("package must include a non-empty 'fragment' (HTML)")

    dash = workspace / ".firm" / "dashboard"
    (dash / "views").mkdir(parents=True, exist_ok=True)
    frag_rel = f"dashboard/views/{vid}.html"
    (dash / "views" / f"{vid}.html").write_text(fragment, encoding="utf-8")

    entry: dict[str, Any] = {"id": vid, "title": title, "mode": mode, "fragment": frag_rel}
    # Only accept action cmds that are argv LISTS — the seam never shells out.
    clean_actions = {}
    for k, a in (package.get("actions") or {}).items():
        if isinstance(a, dict) and isinstance(a.get("cmd"), list):
            clean_actions[str(k)] = {
                "cmd": [str(x) for x in a["cmd"]],
                "timeout": int(a.get("timeout", 60)),
            }
    if clean_actions:
        entry["actions"] = clean_actions
    # Read-only queries (SELECT/WITH only) — so blocks can read the firm DB.
    clean_queries = {
        str(k): q for k, q in (package.get("queries") or {}).items()
        if isinstance(q, str) and re.match(r"^\s*(SELECT|WITH)\b", q, re.I)
    }
    if clean_queries:
        entry["queries"] = clean_queries

    # Extension-contributed blocks (optional): write each fragment + collect entries.
    block_entries: list[dict[str, Any]] = []
    (dash / "blocks").mkdir(parents=True, exist_ok=True)
    for b in (package.get("blocks") or []):
        if not isinstance(b, dict):
            continue
        b_id = str(b.get("id") or "")
        b_frag = b.get("fragment")
        if not _VIEW_ID_RE.fullmatch(b_id) or not isinstance(b_frag, str) or not b_frag.strip():
            continue
        b_mount = str(b.get("mount") or "dashboard")
        if not _VIEW_ID_RE.fullmatch(b_mount):
            b_mount = "dashboard"
        (dash / "blocks" / f"{b_id}.html").write_text(b_frag, encoding="utf-8")
        block_entries.append({"id": b_id, "title": str(b.get("title") or b_id),
                              "mount": b_mount, "fragment": f"dashboard/blocks/{b_id}.html"})

    manifest = dash / "views.json"
    data: dict[str, Any] = {"views": [], "blocks": []}
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"views": [], "blocks": []}
    data["views"] = [v for v in data.get("views", []) if v.get("id") != vid] + [entry]
    if block_entries:
        new_ids = {be["id"] for be in block_entries}
        data["blocks"] = [b for b in data.get("blocks", []) if b.get("id") not in new_ids] + block_entries
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")

    requires = [str(c) for c in (package.get("requires") or []) if isinstance(c, str)]

    install = package.get("install")
    install_result: dict[str, Any] | None = None
    if isinstance(install, dict) and isinstance(install.get("cmd"), list):
        cmd = [str(x) for x in install["cmd"]]
        if confirmed:
            try:
                proc = subprocess.run(
                    cmd, cwd=workspace, capture_output=True, text=True,
                    timeout=int(install.get("timeout", 120)),
                )
                install_result = {
                    "ran": True, "returncode": proc.returncode,
                    "output": (proc.stdout + proc.stderr).strip()[-2000:],
                }
            except subprocess.TimeoutExpired:
                install_result = {"ran": True, "returncode": None, "output": "install timed out"}
            except OSError as exc:
                install_result = {"ran": False, "error": str(exc)}
        else:
            install_result = {
                "ran": False, "skipped": True, "cmd": cmd,
                "description": str(install.get("description") or ""),
            }

    req_status = [{"cmd": c, "present": shutil.which(c) is not None} for c in requires]
    return {
        "ok": True, "view_id": vid, "title": title, "mode": mode,
        "requires": req_status, "install": install_result,
    }


def _firm_file(workspace: Path, rel: str) -> Path:
    """Resolve *rel* inside <workspace>/.firm and refuse escapes."""
    root = (workspace / ".firm").resolve()
    path = (root / rel).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"path {rel!r} escapes the firm directory")
    return path


def read_view_fragment(workspace: Path, view: dict[str, Any]) -> bytes:
    path = _firm_file(workspace, view["fragment"])
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read fragment {view['fragment']!r}: {exc}") from exc


def read_view_file(workspace: Path, view: dict[str, Any], key: str) -> tuple[bytes, str]:
    """Read a manifest-declared data file. Returns (content, content_type)."""
    rel = view["files"].get(key)
    if rel is None:
        raise ValueError(f"file key {key!r} not declared for view {view['id']!r}")
    path = _firm_file(workspace, rel)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {rel!r}: {exc}") from exc
    ctype = "application/json" if rel.endswith(".json") else "text/plain; charset=utf-8"
    return content, ctype


_DIR_CTYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".json": "application/json", ".md": "text/plain; charset=utf-8",
    ".jsonl": "text/plain; charset=utf-8",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
}


def read_view_dir_file(
    workspace: Path, view: dict[str, Any], key: str, filename: str,
) -> tuple[bytes, str]:
    """Read one file from a manifest-declared directory (e.g. game art).

    ``filename`` is a bare basename — no separators, declared extensions only.
    """
    rel = view.get("dirs", {}).get(key)
    if rel is None:
        raise ValueError(f"dir key {key!r} not declared for view {view['id']!r}")
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(f"invalid filename {filename!r}")
    suffix = Path(filename).suffix.lower()
    ctype = _DIR_CTYPES.get(suffix)
    if ctype is None:
        raise ValueError(f"extension {suffix!r} not servable")
    path = _firm_file(workspace, f"{rel}/{filename}")
    try:
        return path.read_bytes(), ctype
    except OSError as exc:
        raise ValueError(f"cannot read {key}/{filename}: {exc}") from exc


_QUERY_PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


def run_view_query(
    db_path: Path, view: dict[str, Any], key: str, params: dict[str, str],
) -> tuple[bytes, str]:
    """Execute a manifest-declared read-only query against the firm DB.

    The manifest names the exact SQL (trust boundary: it lives inside
    ``.firm/``, same as the database); the request only supplies values for
    the query's named ``:params``. SELECT/WITH only — the seam is a read
    surface, writes stay with the firm's own tools.

    Response shaping: a single-row single-column result whose value is a
    JSON document is returned verbatim (the manifest can serve whole render
    feeds, e.g. ``SELECT value FROM game_exports WHERE key='inventory'``);
    anything else returns ``{"rows": [...]}``.
    """
    sql = view.get("queries", {}).get(key)
    if sql is None:
        raise ValueError(f"query {key!r} not declared for view {view['id']!r}")
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I):
        raise ValueError(f"query {key!r} must be a SELECT")
    wanted = set(_QUERY_PARAM_RE.findall(sql))
    missing = wanted - set(params)
    if missing:
        raise ValueError(f"query {key!r} needs params: {', '.join(sorted(missing))}")
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA query_only = ON")
        cur = conn.execute(sql, {k: params[k] for k in wanted})
        cols = [c[0] for c in cur.description or []]
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"query {key!r} failed: {exc}") from exc
    finally:
        conn.close()
    if len(rows) == 1 and len(cols) == 1:
        val = rows[0][0]
        if isinstance(val, str) and val.lstrip()[:1] in ("{", "["):
            return val.encode(), "application/json"
        return json.dumps(val, default=str).encode(), "application/json"
    payload = {"rows": [dict(zip(cols, tuple(r))) for r in rows]}
    return json.dumps(payload, default=str).encode(), "application/json"


def run_view_action(
    workspace: Path, view: dict[str, Any], key: str, body: dict[str, Any],
) -> dict[str, Any]:
    """Execute a manifest-declared view action.

    The firm's own views.json names the exact argv the GUI may invoke —
    the request body only travels as a single JSON argument substituted
    for the ``{json}`` placeholder (never through a shell). Trust boundary:
    the manifest lives inside ``.firm/``, same as the database itself.
    """
    spec = view.get("actions", {}).get(key)
    if spec is None:
        raise ValueError(f"action {key!r} not declared for view {view['id']!r}")
    argv = [str(a) for a in spec.get("cmd") or []]
    if not argv:
        raise ValueError(f"action {key!r} has no cmd")
    payload = json.dumps(body or {}, separators=(",", ":"))
    argv = [a.replace("{json}", payload) for a in argv]
    try:
        proc = subprocess.run(
            argv, cwd=workspace, capture_output=True, text=True,
            timeout=int(spec.get("timeout", 60)),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"action {key!r} timed out"}
    except OSError as exc:
        return {"ok": False, "error": f"action {key!r} failed to exec: {exc}"}
    out = proc.stdout.strip()
    try:
        result = json.loads(out.splitlines()[-1]) if out else {}
    except (json.JSONDecodeError, IndexError):
        result = {"output": out[:2000]}
    ok = proc.returncode == 0 and result.get("ok", True)
    err = (result.get("error") or proc.stderr.strip()[-500:]) if not ok else None
    return {"ok": ok, "result": result, **({"error": err} if err else {})}


_VIEW_PAGE_TEMPLATE = """<!doctype html>
<html lang="en" data-base="__BASE__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>html,body{margin:0;padding:0;min-height:100%;background:#1a1d21}
#viewRoot{padding:24px}</style>
</head><body>
<div id="viewRoot"></div>
<a id="cadreHome" href="__BASE__/" title="Back to the boardroom"
   style="position:fixed;bottom:14px;right:14px;z-index:9;display:grid;place-items:center;
   width:34px;height:34px;border-radius:8px;background:rgba(34,38,43,.8);color:#7e8278;
   border:1px solid rgba(232,233,228,.14);backdrop-filter:blur(3px);text-decoration:none"
   onmouseover="this.style.color='#ffffff'" onmouseout="this.style.color='#7e8278'">
<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg></a>
<script>
/* Minimal CadreShell bridge — same contract the boardroom shell exposes,
   so a fragment renders identically full-page and embedded. */
const BASE = '__BASE__';
window.CadreShell = {
  base: BASE,
  _state: {},
  state: function(){ return this._state; },
  post: async function(path, body){
    try{
      const r = await fetch(BASE + '/api/action/' + path, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {}),
      });
      return await r.json();
    }catch(e){ return {ok: false, error: String(e)}; }
  },
  viewFile: async function(viewId, key){
    const r = await fetch(BASE + '/api/views/' + viewId + '/file/' + key);
    if(!r.ok) throw new Error(key + ' ' + r.status);
    const ct = r.headers.get('Content-Type') || '';
    return ct.includes('json') ? r.json() : r.text();
  },
  viewQuery: async function(viewId, key, params){
    const q = params ? '?' + new URLSearchParams(params) : '';
    const r = await fetch(BASE + '/api/views/' + viewId + '/query/' + key + q);
    if(!r.ok) throw new Error(key + ' ' + r.status);
    return r.json();
  },
  viewAction: async function(viewId, key, body){
    try{
      const r = await fetch(BASE + '/api/views/' + viewId + '/action/' + key, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {}),
      });
      return await r.json();
    }catch(e){ return {ok: false, error: String(e)}; }
  },
};
async function __poll(){
  try{
    const r = await fetch(BASE + '/api/state');
    CadreShell._state = await r.json();
    document.dispatchEvent(new CustomEvent('cadre:state', {detail: CadreShell._state}));
  }catch(e){}
}
try{
  const es = new EventSource(BASE + '/api/events');
  es.addEventListener('change', function(){
    document.dispatchEvent(new CustomEvent('cadre:change'));
    __poll();
  });
}catch(e){}
setInterval(__poll, 15000);
__poll().then(async function(){
  const r = await fetch(BASE + '/api/views/__VIEW_ID__/fragment');
  const root = document.getElementById('viewRoot');
  root.innerHTML = await r.text();
  root.querySelectorAll('script').forEach(function(old){
    const s = document.createElement('script');
    s.textContent = old.textContent;
    old.replaceWith(s);
  });
});
</script></body></html>"""


def render_view_page(view: dict[str, Any], base: str = "") -> bytes:
    """Full-page wrapper for a custom view — the fragment as its own app.

    Same fragment, same CadreShell contract as the boardroom shell; no
    boardroom chrome. Served at ``/view/<id>`` (hub: ``/f/<firm>/view/<id>``).
    """
    return (
        _VIEW_PAGE_TEMPLATE
        .replace("__TITLE__", view["title"])
        .replace("__VIEW_ID__", view["id"])
        .replace("__BASE__", base)
    ).encode()


# ---------------------------------------------------------------------------
# State assembly
# ---------------------------------------------------------------------------

def _member_current_units(
    conn: sqlite3.Connection, member_id: str,
) -> list[dict[str, Any]]:
    out = []
    for u in repo.find(conn, "unit", claimed_by=member_id):
        if u.get("status") in ("pending", "in_progress"):
            out.append({"id": u["id"], "name": u["name"], "status": u["status"]})
    return out


def _run_duration_sec(run: dict[str, Any]) -> float | None:
    started, ended = run.get("started_at"), run.get("ended_at")
    if not (started and ended):
        return None
    try:
        s = datetime.fromisoformat(started)
        e = datetime.fromisoformat(ended)
    except (TypeError, ValueError):
        return None
    # Timestamps may mix tz-aware and naive (e.g. an ended_at written by an older
    # `cadre run end`, or a hand-repaired row). Coerce naive → UTC so the subtraction
    # never raises "can't subtract offset-naive and offset-aware" and crashes the
    # whole firm state render (field failure 2026-07-08: one naive ended_at 500'd the
    # entire wastelander dashboard).
    if s.tzinfo is None:
        s = s.replace(tzinfo=timezone.utc)
    if e.tzinfo is None:
        e = e.replace(tzinfo=timezone.utc)
    return (e - s).total_seconds()


def _parse_pulse_config(contract: dict[str, Any]) -> dict[str, Any]:
    pc = contract.get("pulse_config")
    if isinstance(pc, str) and pc:
        try:
            pc = json.loads(pc)
        except json.JSONDecodeError:
            return {}
    return pc if isinstance(pc, dict) else {}


def _gen_spend_summary(conn: sqlite3.Connection, firm_id: str) -> list[dict[str, Any]]:
    """Boardroom gen-spend line items — no live balance in the poll path (that
    is a network call; the /api/gen-spend endpoint carries it). Tolerant of a
    firm whose DB predates the gen_spend migration."""
    from firm.services import gen_spend
    try:
        return gen_spend.summary(conn, firm_id)
    except Exception:
        return []


def _load_firm_env(workspace: Path) -> None:
    """Best-effort load of the firm's credentials so adapter balance probes
    (e.g. ELEVENLABS_API_KEY) have them. Vault first, then legacy plaintext
    .env. setdefault — never clobbers the process env."""
    try:
        from firm.secrets.provider import resolve_provider
        for k, v in resolve_provider().resolve(workspace).items():
            os.environ.setdefault(k, v)
    except Exception:
        pass   # vault is additive — a broken vault must not take pages down
    env = workspace / ".env"
    if not env.exists():
        return
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def assemble_state(conn: sqlite3.Connection, firm_id: str) -> dict[str, Any]:
    """Build the full dashboard payload from the firm DB."""
    firm = repo.get(conn, "firm", firm_id) or {"id": firm_id, "name": firm_id}

    members = repo.find(conn, "member", firm_id=firm_id)
    contracts = {c["id"]: c for c in repo.find(conn, "contract", firm_id=firm_id)}
    roster = []
    for m in members:
        contract = contracts.get(m.get("contract_id") or "")
        roster.append({
            "id": m["id"],
            "name": m["name"],
            "role": m.get("role"),
            "status": m.get("status"),
            "reports_to": m.get("reports_to_member_id"),
            "contract": contract.get("name") if contract else None,
            "last_activated": m.get("last_activated"),
            "load": compute_load(conn, m["id"]),
            "current_units": _member_current_units(conn, m["id"]),
        })

    operations = repo.find(conn, "operation", firm_id=firm_id)
    projects = repo.find(conn, "project", firm_id=firm_id)
    units = repo.find(conn, "unit", firm_id=firm_id)

    gates = repo.find(conn, "gate", firm_id=firm_id)
    escalations = repo.find(conn, "escalation", firm_id=firm_id)

    # Evidence resolution — every Board item carries its paper trail so the
    # operator never hunts: docs attached to the item's target entity plus
    # any DOC-nnn referenced in the item's own text.
    all_docs = repo.find(conn, "document", firm_id=firm_id)
    docs_by_id = {d["id"]: d for d in all_docs}
    docs_by_parent: dict[str, list[dict[str, Any]]] = {}
    for d in all_docs:
        docs_by_parent.setdefault(str(d.get("parent_entity_id")), []).append(d)

    def _slim(d: dict[str, Any]) -> dict[str, Any]:
        return {"id": d["id"], "name": d["name"], "type": d.get("type")}

    def _related_docs(target_id: str | None, *texts: str | None) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for d in docs_by_parent.get(str(target_id), []):
            found[d["id"]] = d
        blob = " ".join(t for t in texts if t)
        for ref in re.findall(r"DOC-\d+", blob):
            if ref in docs_by_id:
                found[ref] = docs_by_id[ref]
        return [_slim(d) for d in found.values()]

    for g in gates:
        if g.get("status") == "pending":
            g["related_docs"] = _related_docs(
                g.get("target_entity_id"), g.get("action"), g.get("context"),
            )
    for e in escalations:
        if e.get("status") != "resolved":
            e["related_docs"] = _related_docs(
                e.get("target_entity_id"), e.get("title"), e.get("body"),
            )
    goals = repo.find(conn, "goal", firm_id=firm_id)
    documents = repo.find(conn, "document", firm_id=firm_id)

    run_costs = {
        row["run_id"]: row["usd"]
        for row in conn.execute(
            "SELECT run_id, SUM(dollar_equivalent) AS usd FROM usage_event "
            "WHERE firm_id = ? AND run_id IS NOT NULL GROUP BY run_id",
            (firm_id,),
        )
    }
    runs = sorted(
        repo.find(conn, "member_run", firm_id=firm_id),
        key=lambda r: r.get("started_at") or "",
        reverse=True,
    )[:30]
    now = datetime.now(tz=timezone.utc)
    for r in runs:
        r["duration_sec"] = _run_duration_sec(r)
        r["cost_usd"] = run_costs.get(r["id"])
        r.pop("prompt_snapshot", None)
        # Board-facing pre-fill for the rate control — a suggestion the
        # operator confirms or overrides, derived from the run's own signal.
        r["run_score_suggested"] = run_svc.suggest_score(r)
        if r.get("status") == "running":
            # Mirror reap_stale_runs: past 2x contract timeout + grace the
            # spawning pulse is presumed dead — surface that instead of an
            # eternally-green "running" row (Board can't act on a ghost).
            try:
                started = datetime.fromisoformat(r.get("started_at") or "")
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                started = None
            deadline = 2 * _contract_timeout_sec(conn, r["member_id"]) + _REAP_GRACE_SEC
            r["stale"] = (
                started is not None
                and (now - started).total_seconds() > deadline
            )

    records = sorted(
        repo.find(conn, "records", firm_id=firm_id),
        key=lambda r: r.get("timestamp") or "",
        reverse=True,
    )[:50]

    comments = sorted(
        repo.find(conn, "comment", firm_id=firm_id),
        key=lambda c: c.get("created_at") or "",
        reverse=True,
    )[:20]

    costs = conn.execute(
        "SELECT member_id, COUNT(*) AS events, "
        "COALESCE(SUM(dollar_equivalent), 0) AS total_usd "
        "FROM usage_event WHERE firm_id = ? GROUP BY member_id",
        (firm_id,),
    ).fetchall()
    cost_by_member = [dict(c) for c in costs]

    budget_periods = repo.find(conn, "budget_period", firm_id=firm_id)

    contract_settings = []
    for c in contracts.values():
        pc = _parse_pulse_config(c)
        contract_settings.append({
            "id": c["id"],
            "name": c.get("name"),
            "members": [m["name"] for m in members if m.get("contract_id") == c["id"]],
            "model": pc.get("model"),
            "timeout_sec": pc.get("timeout_sec"),
        })

    # The third notification type — a toggleable "runs awaiting your rating"
    # nudge. Count is derived (completed runs with no score); the toggle lives
    # in firm.notify_config where the notifier already reads at fire time.
    unrated_runs = conn.execute(
        "SELECT COUNT(*) FROM member_run "
        "WHERE firm_id = ? AND status = 'completed' AND run_score IS NULL",
        (firm_id,),
    ).fetchone()[0]

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "firm": firm,
        "roster": roster,
        "contract_settings": contract_settings,
        "operations": operations,
        "projects": projects,
        "units": units,
        "gates": gates,
        "escalations": escalations,
        "goals": goals,
        "gen_spend": _gen_spend_summary(conn, firm_id),
        "documents": documents,
        "runs": runs,
        "records": records,
        "comments": comments,
        "cost_by_member": cost_by_member,
        "budget_periods": budget_periods,
        "notify_configured": bool(firm.get("notify_config")),
        "run_review": {
            "nudge_enabled": bool(_json_dict(firm.get("notify_config")).get("run_review_nudge")),
            "unrated_count": unrated_runs,
        },
    }


# ---------------------------------------------------------------------------
# Document content
# ---------------------------------------------------------------------------

def read_document(
    conn: sqlite3.Connection, workspace: Path, doc_id: str,
) -> dict[str, Any]:
    """Resolve a document row and read its content file.

    content_path may be absolute or workspace-relative. Raises ValueError
    for unknown docs / unreadable files (surfaced as a 400 to the UI).
    """
    doc = repo.get(conn, "document", doc_id)
    if not doc:
        raise ValueError(f"document {doc_id!r} not found")
    raw = doc.get("content_path") or ""
    path = Path(raw)
    if not path.is_absolute():
        path = workspace / path
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    comments = [
        c for c in repo.find(conn, "comment", parent_entity_id=doc_id)
        if c.get("parent_entity_type") == "document"
    ]
    comments.sort(key=lambda c: c.get("created_at") or "")
    return {"document": doc, "content": content, "comments": comments}


# ---------------------------------------------------------------------------
# Member profile
# ---------------------------------------------------------------------------

def _instructions_path(workspace: Path, member_id: str) -> Path:
    return workspace / ".firm" / "instructions" / f"{member_id}.md"


def member_profile(
    conn: sqlite3.Connection, workspace: Path, member_id: str,
) -> dict[str, Any]:
    """Everything the profile drawer needs, in one payload."""
    member = repo.get(conn, "member", member_id)
    if not member:
        raise ValueError(f"member {member_id!r} not found")
    firm_id = member["firm_id"]

    contract = repo.get(conn, "contract", member["contract_id"]) if member.get("contract_id") else None

    runs = sorted(
        repo.find(conn, "member_run", member_id=member_id),
        key=lambda r: r.get("started_at") or "", reverse=True,
    )
    durations = [d for d in (_run_duration_sec(r) for r in runs) if d is not None]
    cost_row = conn.execute(
        "SELECT COALESCE(SUM(dollar_equivalent),0) FROM usage_event WHERE member_id = ?",
        (member_id,),
    ).fetchone()
    units_done = conn.execute(
        "SELECT COUNT(*) FROM unit WHERE status='done' "
        "AND (assignee_member_id = ? OR claimed_by = ?)",
        (member_id, member_id),
    ).fetchone()[0]
    escalations_raised = conn.execute(
        "SELECT COUNT(*) FROM escalation WHERE raised_by_member_id = ?",
        (member_id,),
    ).fetchone()[0]

    recent_runs = runs[:10]
    for r in recent_runs:
        r["duration_sec"] = _run_duration_sec(r)
        r.pop("prompt_snapshot", None)
        r["run_score_suggested"] = run_svc.suggest_score(r)

    records = sorted(
        (r for r in repo.find(conn, "records", firm_id=firm_id)
         if r.get("actor_id") == member_id or r.get("target_entity_id") == member_id),
        key=lambda r: r.get("timestamp") or "", reverse=True,
    )[:12]

    notes = [
        c for c in repo.find(conn, "comment", parent_entity_id=member_id)
        if c.get("parent_entity_type") == "member" and not c.get("archived")
    ]
    notes.sort(key=lambda c: c.get("created_at") or "", reverse=True)

    # Artifacts — documents whose producing unit belongs to this member,
    # or that the member authored directly.
    member_unit_ids = {
        u["id"] for u in repo.find(conn, "unit", firm_id=firm_id)
        if u.get("assignee_member_id") == member_id or u.get("claimed_by") == member_id
    }
    artifacts = []
    for d in repo.find(conn, "document", firm_id=firm_id):
        produced = (
            d.get("author_id") == member_id
            or (d.get("parent_entity_type") == "unit"
                and d.get("parent_entity_id") in member_unit_ids)
        )
        if produced:
            artifacts.append({
                "id": d["id"], "name": d["name"], "type": d.get("type") or "doc",
                "content_path": d.get("content_path"),
                "parent_entity_type": d.get("parent_entity_type"),
                "parent_entity_id": d.get("parent_entity_id"),
                "created_at": d.get("created_at"),
            })
    artifacts.sort(key=lambda d: d.get("created_at") or "", reverse=True)

    instructions_file = _instructions_path(workspace, member_id)
    instructions = instructions_file.read_text(encoding="utf-8") if instructions_file.exists() else ""

    # Prompt preview — identity + contract exactly as a run would render
    # them, plus the live unit briefing when one is claimed.
    from firm.pulse.prompt import (
        _render_contract,
        _render_member_identity,
        _render_unit_briefing,
    )
    sections = [
        _render_member_identity(conn, member_id, str(workspace)),
    ]
    contract_section = _render_contract(conn, member_id)
    if contract_section:
        sections.append(contract_section)
    current = _member_current_units(conn, member_id)
    if current:
        sections.append(_render_unit_briefing(conn, current[0]["id"]))
    prompt_preview = "\n\n---\n\n".join(sections)

    completed = sum(1 for r in runs if r.get("status") == "completed")
    failed = sum(1 for r in runs if r.get("status") in ("failed", "timed_out"))

    # Board-only run-score aggregate — derived at read time, never stored.
    # `runs` is sorted newest-first, so scores[:5] is the recent trend window.
    scores = [r["run_score"] for r in runs if r.get("run_score") is not None]
    run_score_avg = round(sum(scores) / len(scores), 1) if scores else None
    recent_scores = scores[:5]
    run_score_recent = (
        round(sum(recent_scores) / len(recent_scores), 1) if recent_scores else None
    )

    return {
        "member": member,
        "contract": contract,
        "contracts": repo.find(conn, "contract", firm_id=firm_id),
        "members": [
            {"id": m["id"], "name": m["name"]}
            for m in repo.find(conn, "member", firm_id=firm_id)
            if m["id"] != member_id
        ],
        "stats": {
            "runs_total": len(runs),
            "runs_completed": completed,
            "runs_failed": failed,
            "success_rate": round(100 * completed / len(runs)) if runs else None,
            "total_cost_usd": cost_row[0],
            "avg_duration_sec": round(sum(durations) / len(durations)) if durations else None,
            "units_done": units_done,
            "escalations_raised": escalations_raised,
            "run_score_avg": run_score_avg,
            "runs_rated": len(scores),
            "run_score_recent": run_score_recent,
        },
        "recent_runs": recent_runs,
        "records": records,
        "notes": notes,
        "artifacts": artifacts,
        "instructions": instructions,
        "current_units": current,
        "prompt_preview": prompt_preview,
    }


def write_instructions(
    conn: sqlite3.Connection, workspace: Path, member_id: str, content: str,
) -> dict[str, Any]:
    """Write the member's standing instructions file (prompt-injected every run)."""
    member = repo.get(conn, "member", member_id)
    if not member:
        raise ValueError(f"member {member_id!r} not found")
    path = _instructions_path(workspace, member_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log_event(
        conn,
        firm_id=member["firm_id"],
        event_type="member.instructions_updated",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
        details={"bytes": len(content.encode())},
    )
    return {"ok": True, "path": str(path)}


# ---------------------------------------------------------------------------
# The Floor — the Board's game layer (v1: derived, read-only)
# ---------------------------------------------------------------------------
# Three laws (planning/cadre-firms/THE-FLOOR-DESIGN.md):
#   1. XP anchors to verified outcomes — zero XP for activity. Runs are a
#      stat and a tenure achievement, never a level driver.
#   2. Derived, never authored — everything below is computed at read time
#      from members, contracts, units, documents, gates, escalations, and
#      usage_event. No writable game state exists anywhere.
#   3. Board-facing only — none of this reaches member prompts or tools.

_XP_UNIT_SHIPPED = 10        # unit closed with a registered deliverable
_XP_GATE_APPROVED = 5        # asked right, asked early
_XP_ESCALATION_ACTIONED = 5  # honesty pays
_LEVEL_FLOORS = [0, 25, 60, 120, 220, 360, 550, 800, 1100, 1500]


def _level_for(xp: int) -> tuple[int, int | None]:
    """Stepped curve over member XP → (level, next threshold, None at cap)."""
    level = 1
    for i, floor_xp in enumerate(_LEVEL_FLOORS):
        if xp >= floor_xp:
            level = i + 1
    return level, (_LEVEL_FLOORS[level] if level < len(_LEVEL_FLOORS) else None)


def _json_dict(v: Any) -> dict[str, Any]:
    """Config columns arrive as dicts or JSON strings depending on the repo
    path that produced them — accept both, hand back {} for anything else."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            parsed = json.loads(v)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _parse_any_ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        ts = datetime.fromisoformat(str(v).replace(" ", "T"))
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _goal_completed(goal: dict[str, Any]) -> bool:
    """True when the goal's metric verifiably sits at its target —
    direction-aware, numbers only. The only jackpot in the game."""
    m = _json_dict(goal.get("metric")) or _json_dict(goal.get("target"))
    cur = m.get("current")
    target = m.get("value", m.get("target"))
    if isinstance(cur, bool) or isinstance(target, bool):
        return False
    if not isinstance(cur, (int, float)) or not isinstance(target, (int, float)):
        return False
    if str(m.get("direction") or "").startswith("lower"):
        return cur <= target
    return cur >= target


def _floor_achievements(
    stats: dict[str, Any], firm_goal_done: bool,
) -> list[dict[str, Any]]:
    """Tenure + craft + honesty tracks, derived from the stats block.
    Discipline ("the seal never tested") waits for denial telemetry (v2)."""
    def rung(track: str, name: str, desc: str, progress: int, target: int):
        return {
            "track": track, "name": name, "desc": desc, "target": target,
            "progress": min(progress, target), "unlocked": progress >= target,
        }
    return [
        rung("service", "Hundred survived", "100 completed runs — veterancy, not velocity",
             stats["runs_survived"], 100),
        rung("service", "Thousand survived", "1,000 completed runs",
             stats["runs_survived"], 1000),
        rung("craft", "First artifact", "first registered deliverable",
             stats["deliverables"], 1),
        rung("craft", "Ten shipped", "10 units closed with a deliverable attached",
             stats["units_shipped"], 10),
        rung("craft", "Fifty shipped", "50 units closed with a deliverable attached",
             stats["units_shipped"], 50),
        rung("craft", "Two hundred shipped", "200 units closed with a deliverable attached",
             stats["units_shipped"], 200),
        rung("honesty", "Raised the flag", "first escalation brought to the Board early",
             stats["escalations_raised"], 1),
        rung("goal", "The number hit",
             "the firm's goal metric reached its target — firm-wide unlock",
             1 if firm_goal_done else 0, 1),
    ]


def floor_state(
    conn: sqlite3.Connection, workspace: Path, firm_id: str,
) -> dict[str, Any]:
    """Everything The Floor renders, in one payload — all derived (law 2)."""
    firm = repo.get(conn, "firm", firm_id) or {"id": firm_id, "name": firm_id}
    members = repo.find(conn, "member", firm_id=firm_id)
    contracts = {c["id"]: c for c in repo.find(conn, "contract", firm_id=firm_id)}
    units = repo.find(conn, "unit", firm_id=firm_id)
    documents = repo.find(conn, "document", firm_id=firm_id)
    gates = repo.find(conn, "gate", firm_id=firm_id)
    escalations = repo.find(conn, "escalation", firm_id=firm_id)
    goals = repo.find(conn, "goal", firm_id=firm_id)

    runs_by_member = {
        r["member_id"]: dict(r)
        for r in conn.execute(
            "SELECT member_id, COUNT(*) AS total, "
            "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS survived, "
            "SUM(CASE WHEN status IN ('failed', 'timed_out') THEN 1 ELSE 0 END) AS failed, "
            "AVG(CASE WHEN run_score IS NOT NULL THEN run_score END) AS avg_score, "
            "SUM(CASE WHEN run_score IS NOT NULL THEN 1 ELSE 0 END) AS rated "
            "FROM member_run WHERE firm_id = ? GROUP BY member_id",
            (firm_id,),
        )
    }
    # Recent-score window per member (newest first) — the calibration trend the
    # Ladder fork can weight. Derived at read time; no stored aggregate.
    recent_scores_by_member: dict[str, list[int]] = {}
    for row in conn.execute(
        "SELECT member_id, run_score FROM member_run "
        "WHERE firm_id = ? AND run_score IS NOT NULL ORDER BY started_at DESC",
        (firm_id,),
    ):
        recent_scores_by_member.setdefault(row["member_id"], []).append(row["run_score"])
    spend_by_member = {
        r["member_id"]: r["usd"]
        for r in conn.execute(
            "SELECT member_id, COALESCE(SUM(dollar_equivalent), 0) AS usd "
            "FROM usage_event WHERE firm_id = ? GROUP BY member_id",
            (firm_id,),
        )
    }

    units_with_docs = {
        str(d.get("parent_entity_id")) for d in documents
        if d.get("parent_entity_type") == "unit"
    }
    leads = {m.get("reports_to_member_id") for m in members} - {None}
    firm_goal_done = any(_goal_completed(g) for g in goals)
    firm_founded = _parse_any_ts(firm.get("created_at"))

    # CLI sockets wear what the machine actually reports (base ext); a broken
    # or absent BASE must never take the Floor down.
    try:
        tool_details = {
            t.get("name"): (t.get("description") or t.get("version") or "")
            for t in (sysconfig_svc.inventory(workspace).get("tools") or [])
        }
    except Exception:
        tool_details = {}

    cards = []
    for m in members:
        mine = [u for u in units
                if u.get("assignee_member_id") == m["id"] or u.get("claimed_by") == m["id"]]
        closed = [u for u in mine if u.get("status") == "done"]
        shipped = [u for u in closed if str(u["id"]) in units_with_docs]
        my_unit_ids = {u["id"] for u in mine}
        deliverables = sum(
            1 for d in documents
            if d.get("author_id") == m["id"]
            or (d.get("parent_entity_type") == "unit"
                and d.get("parent_entity_id") in my_unit_ids)
        )
        my_gates = [g for g in gates if g.get("requesting_member_id") == m["id"]]
        gates_approved = sum(1 for g in my_gates if g.get("status") == "approved")
        my_escs = [e for e in escalations if e.get("raised_by_member_id") == m["id"]]
        escs_actioned = sum(1 for e in my_escs if e.get("status") == "resolved")
        rr = runs_by_member.get(m["id"], {})
        spend = round(float(spend_by_member.get(m["id"]) or 0), 4)

        stats = {
            "runs_total": rr.get("total") or 0,
            "runs_survived": rr.get("survived") or 0,
            "runs_failed": rr.get("failed") or 0,
            "units_closed": len(closed),
            "units_shipped": len(shipped),
            "deliverables": deliverables,
            "gates_raised": len(my_gates),
            "gates_approved": gates_approved,
            "escalations_raised": len(my_escs),
            "escalations_actioned": escs_actioned,
            "spend_usd": spend,
            "cost_per_deliverable": round(spend / deliverables, 2) if deliverables else None,
            "run_score_avg": round(rr["avg_score"], 1) if rr.get("avg_score") is not None else None,
            "runs_rated": rr.get("rated") or 0,
        }
        xp = (_XP_UNIT_SHIPPED * stats["units_shipped"]
              + _XP_GATE_APPROVED * stats["gates_approved"]
              + _XP_ESCALATION_ACTIONED * stats["escalations_actioned"])
        level, next_at = _level_for(xp)

        contract = contracts.get(m.get("contract_id") or "") or {}
        loadout_raw = _json_dict(contract.get("skill_loadout"))
        knowledge = []
        for k in loadout_raw.get("knowledge") or []:
            if isinstance(k, dict):
                path = str(k.get("path") or k.get("name") or "")
                knowledge.append({
                    "name": Path(path).name or path or "attached folder",
                    "teaches": str(k.get("teaches") or ""),
                })
            elif k:
                knowledge.append({"name": str(k), "teaches": ""})
        loadout = {
            "mcp": [str(x) for x in loadout_raw.get("mcp") or []],
            "skills": [str(x) for x in loadout_raw.get("skills") or []],
            "commands": [str(x) for x in loadout_raw.get("commands") or []],
            "cli": [{"name": str(c), "detail": tool_details.get(str(c), "")}
                    for c in loadout_raw.get("cli") or []],
            "knowledge": knowledge,
        }
        # Game-role contracts (dnd-table) author a different loadout shape —
        # scope / duties / sanctioned_commands / style_contract / policies — that
        # the five tool sockets can't represent. Surface it so the sheet renders
        # it as a first-class panel instead of five empty sockets. Board-facing;
        # it is the same authored config _render_contract now boots (no drift).
        role_loadout = {
            "scope": str(loadout_raw.get("scope") or "").strip(),
            "duties": [str(x) for x in loadout_raw.get("duties") or []],
            "sanctioned_commands": [str(x) for x in loadout_raw.get("sanctioned_commands") or []],
            "style_contract": [str(x) for x in loadout_raw.get("style_contract") or []],
            "policies": [str(x) for x in loadout_raw.get("policies") or []],
        }
        validation = _json_dict(contract.get("validation_config"))
        seals = [
            {"match": str(d.get("match") or ""), "reason": str(d.get("reason") or ""),
             "tool": str(d.get("tool") or "")}
            for d in (validation.get("deny") or []) if isinstance(d, dict)
        ]
        oaths = [str(g) for g in validation.get("gates_required") or []]
        pulse = _parse_pulse_config(contract) if contract else {}

        member_created = _parse_any_ts(m.get("created_at"))
        founding = bool(
            firm_founded and member_created
            and abs((member_created - firm_founded).total_seconds()) <= 3600
        )

        cards.append({
            "id": m["id"],
            "name": m["name"],
            "role": m.get("role"),
            "status": m.get("status"),
            "owns": m.get("description") or "",
            "lead": m["id"] in leads,
            "reports_to": m.get("reports_to_member_id"),
            "tenure": {"founding": founding, "since": m.get("created_at")},
            "budget": {"model": pulse.get("model"),
                       "timeout_sec": pulse.get("timeout_sec")},
            "loadout": loadout,
            "role_loadout": role_loadout,
            "oaths": oaths,
            "seals": seals,
            "stats": stats,
            "xp": xp,
            "level": level,
            "level_floor": _LEVEL_FLOORS[level - 1],
            "level_next_at": next_at,
            "achievements": _floor_achievements(stats, firm_goal_done),
        })

    cards.sort(key=lambda c: c["id"])

    # Calibration aggregate — the derived score signal the Calibration Ladder
    # (separate fork) weights for tier graduation. Exposed here as read-time
    # derivation only; the tier model is NOT defined in this fork.
    calibration = {}
    for m in members:
        rr = runs_by_member.get(m["id"], {})
        recent = recent_scores_by_member.get(m["id"], [])[:5]
        calibration[m["id"]] = {
            "avg": round(rr["avg_score"], 2) if rr.get("avg_score") is not None else None,
            "rated": rr.get("rated") or 0,
            "total": rr.get("total") or 0,
            "recent_avg": round(sum(recent) / len(recent), 2) if recent else None,
        }

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "firm": {"id": firm.get("id"), "name": firm.get("name"),
                 "founded_at": firm.get("created_at")},
        "goal_completed": firm_goal_done,
        "members": cards,
        "calibration": calibration,
    }


# ---------------------------------------------------------------------------
# Board actions
# ---------------------------------------------------------------------------

def perform_action(
    conn: sqlite3.Connection,
    action: str,
    entity_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch a Board action. Raises ValueError on invalid input."""
    if action == "gate-approve":
        data = {"approver_comment": body["comment"]} if body.get("comment") else None
        return gate_svc.approve_gate(conn, entity_id, data)
    if action == "gate-reject":
        data = {"approver_comment": body["comment"]} if body.get("comment") else None
        return gate_svc.reject_gate(conn, entity_id, data)
    if action == "gate-dismiss":
        # Notification layer only — clears the badge, never decides the gate.
        return gate_svc.dismiss_gate(conn, entity_id)
    if action == "escalation-acknowledge":
        return escalation_svc.resolve_escalation(
            conn, entity_id, status="acknowledged",
            resolution=body.get("resolution"),
        )
    if action == "escalation-resolve":
        return escalation_svc.resolve_escalation(
            conn, entity_id, status="resolved",
            resolution=body.get("resolution"),
        )
    if action == "goal-metric":
        fields = {
            k: body[k]
            for k in ("current", "value", "unit", "metric_type", "deadline", "trend")
            if body.get(k) not in (None, "")
        }
        return goal_svc.update_goal_metric(conn, entity_id, **fields)
    if action == "goal-create":
        # The Board authors success criteria; Members only propose (their MCP
        # tool raises a create-goal Gate). This was inverted at birth — every
        # write was gated except the one that defines what winning means.
        # entity_id = the parent entity id; its type rides in the body.
        goal_data: dict[str, Any] = {
            "target": body.get("target"),
            "parent_entity_type": body.get("parent_entity_type"),
            "parent_entity_id": entity_id,
        }
        for k in ("metric", "level", "status"):
            if body.get(k) not in (None, ""):
                goal_data[k] = body[k]
        return goal_svc.create_goal(conn, firm_id_of(conn, body), goal_data)
    if action == "comment-create":
        # entity_id carries the parent type; the id rides in the body.
        return comment_svc.create_comment(conn, firm_id_of(conn, body), {
            "parent_entity_type": entity_id,
            "parent_entity_id": body.get("parent_entity_id"),
            "body": body.get("body"),
            "author_type": "board",
        })
    if action == "member-update":
        data = {
            k: body[k]
            for k in ("role", "description", "status", "contract_id")
            if body.get(k) not in (None, "")
        }
        if "reports_to_member_id" in body:
            # Explicit null/"" means "reports to the Board" — a real change.
            data["reports_to_member_id"] = body["reports_to_member_id"] or None
        if not data:
            raise ValueError("No member fields to update")
        return member_svc.update_member(conn, entity_id, data)
    if action == "doc-revision":
        return document_svc.request_revision(
            conn,
            firm_id_of(conn, body),
            entity_id,
            body.get("body") or "",
        )
    if action == "contract-model":
        # Per-contract model override (pulse_config.model — the cost lever).
        # entity_id = contract id; body.model = alias/full id, empty = inherit
        # the account default. Takes effect on the next spawn; no restart.
        contract = repo.get(conn, "contract", entity_id)
        if not contract:
            raise ValueError(f"unknown contract {entity_id!r}")
        pc = _parse_pulse_config(contract)
        model = str(body.get("model") or "").strip()
        if model:
            pc["model"] = model
        else:
            pc.pop("model", None)
        repo.update(conn, "contract", entity_id, {"pulse_config": json.dumps(pc)})
        log_event(
            conn,
            firm_id=contract["firm_id"],
            event_type="contract.updated",
            actor={"type": "board", "id": None},
            target_ref={"type": "contract", "id": entity_id},
            details={"pulse_config.model": model or "(inherit default)"},
        )
        return {"contract_id": entity_id, "model": model or None}
    if action == "run-retry":
        # Re-queue a failed/timed-out run's unit: release any stale claim so
        # the next pulse re-spawns the member on it. entity_id = run id.
        run = repo.get(conn, "member_run", entity_id)
        if not run:
            raise ValueError(f"run {entity_id!r} not found")
        if run.get("status") not in ("failed", "timed_out"):
            raise ValueError(f"run {entity_id!r} is {run.get('status')!r} — only "
                             "failed/timed_out runs can be retried")
        unit = repo.get(conn, "unit", run.get("unit_id") or "")
        if not unit:
            raise ValueError(f"run {entity_id!r} has no unit to retry")
        if unit.get("status") not in ("pending", "in_progress", "blocked"):
            raise ValueError(f"unit {unit['id']} is {unit.get('status')!r} — "
                             "nothing left to retry")
        if unit.get("claimed_by"):
            unit_svc.release_unit(conn, unit["id"])
        if unit.get("status") != "pending":
            unit_svc.update_unit(conn, unit["id"], {"status": "pending"})
        log_event(
            conn,
            firm_id=run["firm_id"],
            event_type="run.retry_requested",
            actor={"type": "board", "id": None},
            target_ref={"type": "unit", "id": unit["id"]},
            details={"failed_run": entity_id, "member": run.get("member_id")},
        )
        return {"unit": unit["id"],
                "note": "re-queued — the member retries it on the next pulse"}
    if action == "unit-create":
        data: dict[str, Any] = {
            "name": body.get("name"),
            "project_id": body.get("project_id"),
        }
        if body.get("description"):
            data["description"] = body["description"]
        if body.get("assignee_member_id"):
            data["assignee_member_id"] = body["assignee_member_id"]
        if body.get("priority"):
            data["priority"] = body["priority"]
        if body.get("model"):
            # Per-unit model override — beats the contract for this run.
            data["model"] = body["model"]
        if data.get("name") is None or data.get("project_id") is None:
            raise ValueError("name and project_id are required")
        return unit_svc.create_unit(conn, firm_id_of(conn, body), data)
    if action == "run-score":
        # Board rates a completed run 1-5 (+ optional note). One path serves
        # the initial score and every rescore; the score feeds Floor stats and
        # (later) the Calibration Ladder, and is NEVER shown to the member.
        # entity_id = run id.
        return run_svc.score_run(
            conn, entity_id, body.get("score"), body.get("notes"),
            actor={"type": "board", "id": None},
        )
    if action == "firm-setting":
        # Board toggles a per-firm boolean setting, persisted in
        # firm.notify_config (where notify.get_notify_config reads it at fire
        # time). entity_id = setting key; body.value = new value. Mirrors the
        # contract-model config-write idiom.
        allowed = {"run_review_nudge"}
        if entity_id not in allowed:
            raise ValueError(f"unknown firm setting {entity_id!r}")
        setting_firm_id = firm_id_of(conn, body)
        firm = repo.get(conn, "firm", setting_firm_id)
        if not firm:
            raise ValueError(f"unknown firm {setting_firm_id!r}")
        cfg = _json_dict(firm.get("notify_config"))
        cfg[entity_id] = bool(body.get("value"))
        repo.update(conn, "firm", setting_firm_id, {"notify_config": json.dumps(cfg)})
        log_event(
            conn,
            firm_id=setting_firm_id,
            event_type="firm.setting_updated",
            actor={"type": "board", "id": None},
            target_ref={"type": "firm", "id": setting_firm_id},
            details={entity_id: cfg[entity_id]},
        )
        return {"firm_id": setting_firm_id, "key": entity_id, "value": cfg[entity_id]}
    raise ValueError(f"Unknown action {action!r}")


def firm_id_of(conn: sqlite3.Connection, body: dict[str, Any]) -> str:
    """Firm scope for creates: explicit in body, else the DB's sole firm."""
    if body.get("firm_id"):
        return str(body["firm_id"])
    firms = repo.find(conn, "firm")
    if len(firms) == 1:
        return firms[0]["id"]
    raise ValueError("firm_id required (multiple firms in this DB)")


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def _http_send(
    h: BaseHTTPRequestHandler,
    status: int,
    payload: dict | bytes,
    content_type: str = "application/json",
) -> None:
    body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


def _send_media(h: BaseHTTPRequestHandler, content: bytes, ctype: str) -> None:
    """Dir-file responses with HTTP Range support — browsers refuse to seek
    audio/video without byte ranges, so scrubbing depends on this."""
    total = len(content)
    rng = h.headers.get("Range") or ""
    if rng.startswith("bytes="):
        start_s, _, end_s = rng[6:].partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = min(int(end_s) if end_s else total - 1, total - 1)
        except ValueError:
            start, end = 0, total - 1
        if 0 <= start <= end:
            chunk = content[start:end + 1]
            h.send_response(206)
            h.send_header("Content-Type", ctype)
            h.send_header("Content-Length", str(len(chunk)))
            h.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            h.send_header("Accept-Ranges", "bytes")
            h.send_header("Cache-Control", "no-store")
            h.end_headers()
            h.wfile.write(chunk)
            return
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(total))
    h.send_header("Accept-Ranges", "bytes")
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(content)


def _serve_index(h: BaseHTTPRequestHandler, base: str = "") -> None:
    body = _INDEX_HTML.read_bytes()
    if base:
        # The SPA reads document.documentElement.dataset.base to prefix
        # every fetch/EventSource URL — one injection point, zero rewrites.
        body = body.replace(b"<html", f'<html data-base="{base}"'.encode(), 1)
    _http_send(h, 200, body, "text/html; charset=utf-8")


def _sse_stream(h: BaseHTTPRequestHandler, db_path: Path) -> None:
    """SSE push: tell the client the moment the firm changes — escalations,
    gates, runs, game turns land sub-second instead of on the poll cadence.

    Change signal, in order of preference:
      1. ``PRAGMA data_version`` — bumped by any other connection's commit
         (local SQLite, self-hosted sqld).
      2. ``firm_rev.n`` — the write counter every firm write path bumps
         (Turso cloud refuses the pragma; see core.db.bump_rev).
    Stdlib-only; EventSource auto-reconnects."""
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    conn = connect(db_path)

    def _token() -> tuple:
        row = conn.execute("PRAGMA data_version").fetchone()
        if row is not None:
            return ("dv", row[0])
        from firm.core.db import get_rev
        return ("rev", get_rev(conn))

    # Remote backends pay a network round-trip per check — ease the tick.
    interval = 1.0 if db_is_remote() else 0.5
    try:
        last = _token()
        h.wfile.write(b"retry: 2000\n\n")
        h.wfile.flush()
        ticks = 0
        while True:
            time.sleep(interval)
            ticks += 1
            cur = _token()
            if cur != last:
                last = cur
                h.wfile.write(b"event: change\ndata: {}\n\n")
                h.wfile.flush()
            elif ticks % 30 == 0:
                h.wfile.write(b": ping\n\n")  # keep-alive
                h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass  # client went away — thread ends
    finally:
        conn.close()


def _claude_project_dir(workspace: Path) -> Path:
    """``~/.claude/projects/<hyphenated-abs-workspace>`` — where the claude CLI
    streams each headless member run's transcript live (appended as the agent
    works). The dir name is the absolute workspace path with ``/`` and ``.``
    replaced by ``-``."""
    slug = str(workspace).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / slug


def _summarize_tool(name: str, inp: dict[str, Any]) -> str:
    """One-line human summary of a tool call for the live spy log."""
    if not isinstance(inp, dict):
        return ""
    if name == "Bash":
        return str(inp.get("command", ""))[:400]
    if name in ("Write", "Edit", "Read", "NotebookEdit"):
        return str(inp.get("file_path", ""))
    if name in ("Grep", "Glob"):
        return str(inp.get("pattern", ""))[:200]
    for k, v in inp.items():
        if isinstance(v, str) and v.strip():
            return f"{k}={v[:200]}"
    return ""


def _run_action_log(
    workspace: Path, db_path: Path, run_id: str, cursor: int,
) -> dict[str, Any]:
    """Read-only spy over a headless member run: tail the claude transcript and
    return the action items (model text + each tool call) AFTER *cursor* (a line
    index). Firms run one member at a time, so the newest transcript created at
    or after the run's start is that run's."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT started_at, ended_at, status FROM member_run WHERE id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"items": [], "cursor": cursor, "running": False, "found": False}
    running = row["status"] == "running"

    proj = _claude_project_dir(workspace)
    transcripts = (
        sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if proj.exists() else []
    )
    st_epoch = 0.0
    if row["started_at"]:
        try:
            st = datetime.fromisoformat(row["started_at"])
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
            st_epoch = st.timestamp() - 30  # slack for clock skew / startup lag
        except (TypeError, ValueError):
            st_epoch = 0.0
    chosen = None
    for p in reversed(transcripts):  # newest first
        if p.stat().st_mtime >= st_epoch:
            chosen = p
            break
    if chosen is None:
        return {"items": [], "cursor": cursor, "running": running,
                "found": True, "waiting": True}

    lines = chosen.read_text(encoding="utf-8", errors="replace").splitlines()
    items: list[dict[str, Any]] = []
    for ln in lines[cursor:]:
        try:
            obj = json.loads(ln)
        except (json.JSONDecodeError, TypeError):
            continue
        msg = obj.get("message") or {}
        role, content = msg.get("role"), msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if role == "assistant" and b.get("type") == "text" and (b.get("text") or "").strip():
                items.append({"kind": "text", "text": b["text"].strip()[:600]})
            elif role == "assistant" and b.get("type") == "tool_use":
                items.append({"kind": "tool", "tool": b.get("name", "?"),
                              "detail": _summarize_tool(b.get("name", ""), b.get("input") or {})})
            elif role == "user" and b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                txt = str(c or "").strip().replace("\n", " ")
                if txt:
                    items.append({"kind": "result", "text": txt[:200]})
    if len(items) > 800:
        items = items[-800:]
    return {"items": items, "cursor": len(lines), "running": running, "found": True}


def _sysconfig_get(h: BaseHTTPRequestHandler, workspace: Path, path: str) -> None:
    """GET /api/sysconfig[/…] — platform-aware firm workspace config reads."""
    route, _, qs = path.partition("?")
    parts = route.strip("/").split("/")   # api / sysconfig / ...
    try:
        if parts == ["api", "sysconfig"]:
            _http_send(h, 200, sysconfig_svc.describe(workspace))
        elif parts[2:] == ["fs"]:
            params = {k: v[-1] for k, v in parse_qs(qs).items()}
            _http_send(h, 200, sysconfig_svc.fs_browse(
                params.get("path"), params.get("q")))
        elif parts[2:] == ["vars"]:
            _http_send(h, 200, sysconfig_svc.vars_list(workspace))
        elif parts[2:] == ["mcp"]:
            _http_send(h, 200, sysconfig_svc.mcp_list(workspace))
        elif parts[2:] == ["inventory"]:
            _http_send(h, 200, sysconfig_svc.inventory(workspace))
        elif len(parts) == 4 and parts[2] == "file":
            _http_send(h, 200, sysconfig_svc.read_file(workspace, parts[3]))
        else:
            _http_send(h, 404, {"error": f"unknown sysconfig route {route!r}"})
    except (ValueError, VaultError) as exc:
        _http_send(h, 400, {"ok": False, "error": str(exc)})


def _sysconfig_post(
    h: BaseHTTPRequestHandler,
    workspace: Path,
    db_path: Path,
    firm_id: str,
    path: str,
    body: dict[str, Any],
) -> None:
    """POST /api/sysconfig/… — audited config writes (Records on every one)."""
    parts = path.strip("/").split("/")
    conn = connect(db_path)
    try:
        sub = parts[2:]
        if sub[:1] == ["file"] and len(sub) == 2:
            result = sysconfig_svc.write_file(
                conn, firm_id, workspace, sub[1], str(body.get("content") or ""))
        elif sub == ["vars"]:
            result = sysconfig_svc.vars_set(
                conn, firm_id, workspace,
                str(body.get("key") or ""), body.get("value"),
                str(body.get("tier") or "firm"))
        elif sub == ["vars", "delete"]:
            result = sysconfig_svc.vars_delete(
                conn, firm_id, workspace,
                str(body.get("key") or ""), str(body.get("tier") or "firm"))
        elif sub == ["vars", "reveal"]:
            result = sysconfig_svc.vars_reveal(workspace, str(body.get("key") or ""))
        elif sub == ["vars", "import"]:
            result = sysconfig_svc.vars_import(
                conn, firm_id, workspace, scrub=bool(body.get("scrub")))
        elif sub == ["mcp"]:
            op = str(body.get("op") or "set")
            name = str(body.get("name") or "")
            if op == "remove":
                result = sysconfig_svc.mcp_remove(conn, firm_id, workspace, name)
            else:
                result = sysconfig_svc.mcp_set(
                    conn, firm_id, workspace, name, body.get("spec") or {})
        elif sub == ["tools", "install"]:
            result = sysconfig_svc.tool_install(
                conn, firm_id, workspace, str(body.get("source") or ""))
        else:
            _http_send(h, 404, {"ok": False, "error": "unknown sysconfig route"})
            return
        _http_send(h, 200, {"ok": True, "result": result})
    except (ValueError, VaultError) as exc:
        _http_send(h, 400, {"ok": False, "error": str(exc)})
    finally:
        conn.close()


def _firm_get(
    h: BaseHTTPRequestHandler,
    workspace: Path,
    db_path: Path,
    firm_id: str,
    path: str,
    base: str = "",
) -> None:
    """GET routing for one firm's boardroom. *path* is firm-relative
    (hub strips its /f/<firm> prefix); *base* prefixes client-side URLs."""
    if path in ("/", "/index.html"):
        _serve_index(h, base)
        return
    if path == "/api/events":
        _sse_stream(h, db_path)
        return
    if path == "/api/state":
        conn = connect(db_path)
        try:
            _http_send(h, 200, assemble_state(conn, firm_id))
        finally:
            conn.close()
        return
    if path == "/api/gen-spend":
        _load_firm_env(workspace)   # adapter balance probes need the firm's API keys
        conn = connect(db_path)
        try:
            from firm.services import gen_spend
            _http_send(h, 200, {"gen_spend": gen_spend.summary(
                conn, firm_id, with_balance=True)})
        finally:
            conn.close()
        return
    if path == "/api/pulse-status":
        status_file = workspace / ".firm" / "last-pulse.json"
        if not status_file.exists():
            _http_send(h, 200, {"available": False})
            return
        try:
            parsed = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = None
        _http_send(h, 200, {
            "available": True,
            "mtime": status_file.stat().st_mtime,
            "summary": parsed,
        })
        return
    if path.startswith("/api/run/") and "/actions" in path:
        route, _, qs = path.partition("?")
        parts = route.strip("/").split("/")  # api / run / <id> / actions
        run_id = parts[2] if len(parts) >= 4 else ""
        cursor = 0
        for kv in qs.split("&"):
            if kv.startswith("cursor="):
                try:
                    cursor = max(0, int(kv.split("=", 1)[1]))
                except ValueError:
                    cursor = 0
        _http_send(h, 200, _run_action_log(workspace, db_path, run_id, cursor))
        return
    if path.startswith("/view/"):
        vid = path.strip("/").split("/")[1].split("?")[0]
        views = {v["id"]: v for v in load_custom_views(workspace)}
        if vid in views:
            _http_send(h, 200, render_view_page(views[vid], base), "text/html; charset=utf-8")
        else:
            _http_send(h, 404, {"error": f"unknown view {vid!r}"})
        return
    if path == "/api/blocks":
        blocks = load_custom_blocks(workspace)
        _http_send(h, 200, {"blocks": [
            {"id": b["id"], "title": b["title"], "mount": b["mount"]} for b in blocks
        ]})
        return
    if path.startswith("/api/blocks/"):
        route, _, _ = path.partition("?")
        parts = route.strip("/").split("/")  # api / blocks / <id> / fragment
        blocks = {b["id"]: b for b in load_custom_blocks(workspace)}
        block = blocks.get(parts[2]) if len(parts) >= 4 else None
        try:
            if block is None:
                raise ValueError(f"unknown block {parts[2] if len(parts) >= 3 else ''!r}")
            if parts[3] == "fragment" and len(parts) == 4:
                _http_send(h, 200, read_view_fragment(workspace, block),
                           "text/html; charset=utf-8")
            else:
                raise ValueError("unknown block route")
        except ValueError as exc:
            _http_send(h, 404, {"error": str(exc)})
        return
    if path == "/api/views":
        views = load_custom_views(workspace)
        _http_send(h, 200, {"views": [
            {"id": v["id"], "title": v["title"], "mode": v["mode"],
             "files": sorted(v["files"]), "queries": sorted(v["queries"])}
            for v in views
        ]})
        return
    if path.startswith("/api/views/"):
        route, _, qs = path.partition("?")
        parts = route.strip("/").split("/")
        # /api/views/<id>/fragment | .../file/<key> | .../query/<key>?p=v
        views = {v["id"]: v for v in load_custom_views(workspace)}
        view = views.get(parts[2]) if len(parts) >= 4 else None
        try:
            if view is None:
                raise ValueError("unknown view")
            if parts[3] == "fragment" and len(parts) == 4:
                _http_send(h, 200, read_view_fragment(workspace, view),
                           "text/html; charset=utf-8")
            elif parts[3] == "file" and len(parts) == 5:
                content, ctype = read_view_file(workspace, view, parts[4])
                _http_send(h, 200, content, ctype)
            elif parts[3] == "query" and len(parts) == 5:
                params = {k: v[-1] for k, v in parse_qs(qs).items()}
                content, ctype = run_view_query(db_path, view, parts[4], params)
                _http_send(h, 200, content, ctype)
            elif parts[3] == "dir" and len(parts) == 6:
                content, ctype = read_view_dir_file(
                    workspace, view, parts[4], parts[5])
                _send_media(h, content, ctype)
            else:
                raise ValueError("unknown view route")
        except ValueError as exc:
            _http_send(h, 404, {"error": str(exc)})
        return
    if path == "/api/floor":
        conn = connect(db_path)
        try:
            _http_send(h, 200, floor_state(conn, workspace, firm_id))
        finally:
            conn.close()
        return
    if path == "/api/inventory" or path.startswith("/api/inventory?"):
        # The Armory — machine-tier, firm-agnostic; feeds the equip picker.
        from firm.dashboard import inventory as inventory_mod
        _, _, qs = path.partition("?")
        params = {k: v[-1] for k, v in parse_qs(qs).items()}
        _http_send(h, 200, inventory_mod.view(
            kind=params.get("kind") or None,
            q=params.get("q") or "",
            include_excluded=params.get("all") == "1",
        ))
        return
    if path.startswith("/api/member/"):
        member_id = path.rsplit("/", 1)[1]
        conn = connect(db_path)
        try:
            _http_send(h, 200, member_profile(conn, workspace, member_id))
        except ValueError as exc:
            _http_send(h, 400, {"error": str(exc)})
        finally:
            conn.close()
        return
    if path.startswith("/api/doc/"):
        doc_id = path.rsplit("/", 1)[1]
        conn = connect(db_path)
        try:
            _http_send(h, 200, read_document(conn, workspace, doc_id))
        except ValueError as exc:
            _http_send(h, 400, {"error": str(exc)})
        finally:
            conn.close()
        return
    if path == "/api/sysconfig" or path.startswith("/api/sysconfig/"):
        _sysconfig_get(h, workspace, path)
        return
    _http_send(h, 404, {"error": "not found"})


def _slack_token_from_workspace(workspace: Path) -> str | None:
    """Best-effort CADRE_SLACK_TOKEN — the vault is the home for it now;
    the .mcp.json regex remains as the legacy fallback for firms that
    predate the vault and still carry the token inline."""
    try:
        from firm.secrets.provider import resolve_provider
        token = resolve_provider().resolve(workspace).get("CADRE_SLACK_TOKEN")
        if token:
            return token
    except Exception:
        pass
    mcp = workspace / ".mcp.json"
    if not mcp.exists():
        return None
    m = re.search(
        r"CADRE_SLACK_TOKEN=([^\s\"']+)",
        mcp.read_text(encoding="utf-8", errors="replace"),
    )
    return m.group(1) if m else None


def _venv_python(workspace: Path) -> str:
    """The firm's OWN venv interpreter, never the hub's — a hub started from
    one firm's venv must not run another firm's pulse with the wrong package
    (field failure 2026-07-13: dnd-table's python executed crows-and-pawns'
    workspace). Falls back to this interpreter when the firm has no venv."""
    for rel in (("bin", "python"), ("Scripts", "python.exe")):
        candidate = workspace / ".venv" / Path(*rel)
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _pulse_path(workspace: Path) -> str:
    """A full PATH for the dispatched pulse.

    systemd ``--user`` starts with a BARE PATH (no ``~/.local/bin``, no
    ``.firm/bin``). After a host/WSL restart the detached pulse then can't
    resolve firm tools and every member skips (ESC-008/009/010/015), with the
    only unblock being a manual ``systemctl --user import-environment PATH``.
    Carry a real PATH onto the dispatch so neither the preflight nor the member
    spawns ever run bare — firm-local dirs first, then the inherited PATH, then a
    system floor in case the hub's own PATH was thin.
    """
    home = Path.home()
    lead = [str(home / ".local" / "bin"), str(workspace / ".firm" / "bin")]
    base_bin = shutil.which("base")
    if base_bin:
        lead.insert(0, str(Path(base_bin).parent))
    floor = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin",
             "/sbin", "/bin"]
    ordered: list[str] = []
    seen: set[str] = set()
    for chunk in lead + [os.environ.get("PATH") or ""] + floor:
        for seg in chunk.split(os.pathsep):
            if seg and seg not in seen:
                seen.add(seg)
                ordered.append(seg)
    return os.pathsep.join(ordered)


def _fire_pulse(
    workspace: Path, firm_id: str, only: str | None = None,
) -> dict[str, Any]:
    """Board-initiated pulse — detached through the platform scheduler so
    member runs survive this HTTP request (a pulse blocks until its slowest
    member finishes; never run it inside a request thread). With *only*, a
    Board-targeted pulse activating a single Member."""
    from firm.sched import resolve_scheduler

    unit = f"pulse-{firm_id}-{int(time.time())}"
    env = {"FIRM_ID": firm_id}
    claude_bin = os.environ.get("CADRE_CLAUDE_BIN")
    if not claude_bin:
        from firm.pulse.spawn import resolve_claude_bin
        claude_bin, _ = resolve_claude_bin()
    if claude_bin:
        env["CADRE_CLAUDE_BIN"] = claude_bin
    token = _slack_token_from_workspace(workspace)
    if token:
        env["CADRE_SLACK_TOKEN"] = token
    # systemd --user starts with a bare PATH — carry a full one so the dispatched
    # pulse (preflight + member spawns) resolves firm tools without a manual
    # `systemctl --user import-environment PATH` after a host restart.
    env["PATH"] = _pulse_path(workspace)

    pulse_argv = [
        _venv_python(workspace), "-m", "firm", "pulse",
        "--workspace", str(workspace), "--firm-id", firm_id,
    ]
    if only:
        pulse_argv += ["--only", only]
    # Each pulse writes its own log, then claims last-pulse.json on
    # completion — concurrent pulses previously redirected into the same
    # file and the second clobbered the first mid-write. /api/pulse-status
    # keeps reading last-pulse.json (last finisher wins, which is what
    # "last pulse" means); the per-pulse logs are the durable record.
    # The redirect wrapper is Python, not a shell, so it runs on any OS.
    log_dir = workspace / ".firm" / "pulse-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pulse_log = log_dir / f"{unit}.json"
    status_file = workspace / ".firm" / "last-pulse.json"
    wrapper = (
        "import shutil, subprocess, sys; "
        f"rc = subprocess.call({pulse_argv!r}, "
        f"stdout=open({str(pulse_log)!r}, 'w'), stderr=subprocess.STDOUT); "
        f"shutil.copy({str(pulse_log)!r}, {str(status_file)!r}); "
        "sys.exit(rc)"
    )
    try:
        dispatched = resolve_scheduler().spawn_detached(
            [_venv_python(workspace), "-c", wrapper],
            workdir=workspace, env=env, unit=unit,
        )
    except OSError as exc:
        raise ValueError(f"pulse dispatch failed: {exc}") from exc
    monitor = (f"journalctl --user -u {unit}"
               if dispatched.get("via") == "systemd-run" else str(pulse_log))
    return {"unit": unit, "monitor": monitor, "via": dispatched.get("via")}


def create_commission(
    conn: sqlite3.Connection,
    workspace: Path,
    firm_id: str,
    member_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Board commission: one-shot instructions to a single Member.

    Creates a real Unit (so briefing assembly, validation, completion
    persistence, Records, and budget all apply exactly as in a pulse),
    claims it for the Member, then fires a Member-targeted pulse. Nothing
    here is a side channel — the commission IS firm work, just Board-authored.
    """
    member = repo.get(conn, "member", member_id)
    if not member or member.get("firm_id") != firm_id:
        raise ValueError(f"unknown member {member_id!r}")
    if member.get("status") != "active":
        raise ValueError(f"{member_id} is {member.get('status')} — activate first")
    instructions = str(body.get("instructions") or "").strip()
    if not instructions:
        raise ValueError("instructions required")

    project_id = body.get("project_id")
    if not project_id:
        projects = [
            p for p in repo.find(conn, "project", firm_id=firm_id)
            if p.get("status") in ("in_progress", "active")
        ]
        if not projects:
            raise ValueError("no active project to attach the commission to")
        project_id = projects[0]["id"]

    title = instructions.splitlines()[0][:70]
    unit = unit_svc.create_unit(conn, firm_id, {
        "name": f"Board commission: {title}",
        "project_id": project_id,
        "description": instructions,
        "assignee_member_id": member_id,
        "priority": "high",
        "tags": ["board-commission"],
        "acceptance_criteria": ["Fulfill the Board's commission as written in the description"],
    })
    repo.update(conn, "unit", unit["id"], {"claimed_by": member_id})
    log_event(
        conn,
        firm_id=firm_id,
        event_type="board.commission",
        actor={"type": "board", "id": None},
        target_ref={"type": "unit", "id": unit["id"]},
        details={"member_id": member_id, "instructions": instructions[:500]},
    )
    dispatch = _fire_pulse(workspace, firm_id, only=member_id)
    return {"unit": unit, "dispatch": dispatch}


_EQUIP_KINDS = ("mcp", "skills", "commands", "cli", "knowledge")


def _member_loadout(
    conn: sqlite3.Connection, firm_id: str, member_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(contract row, parsed loadout with all five keys) — or a loud error."""
    member = repo.get(conn, "member", member_id)
    if not member or member.get("firm_id") != firm_id:
        raise ValueError(f"unknown member {member_id!r}")
    if not member.get("contract_id"):
        raise ValueError(f"{member_id} has no contract — run Train to wire one first")
    contract = repo.get(conn, "contract", member["contract_id"])
    if not contract:
        raise ValueError(f"contract {member['contract_id']!r} not found")
    loadout = _json_dict(contract.get("skill_loadout"))
    for k in _EQUIP_KINDS:
        v = loadout.get(k)
        loadout[k] = v if isinstance(v, list) else []
    return contract, loadout


def equip_member(
    conn: sqlite3.Connection,
    workspace: Path,
    firm_id: str,
    member_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Board equips one item onto a Member — the same audited path Train uses.

    skills/commands/cli are loadout entries (the machine already carries the
    thing; the loadout is who wears it). An MCP equip additionally materializes
    the server's spec into the firm's ``.mcp.json`` through the sysconfig write
    Train uses (timestamped backup + Records) — specs resolve LIVE via
    ``discovery.raw_specs``, secrets ride as ``${KEY}`` placeholders for the
    vault, never as values. knowledge attaches a {path, teaches} folder.
    """
    kind = str(body.get("kind") or "")
    if kind not in _EQUIP_KINDS:
        raise ValueError(f"unknown equip kind {kind!r}")
    contract, loadout = _member_loadout(conn, firm_id, member_id)

    needs_keys: list[str] = []
    if kind == "knowledge":
        path = str(body.get("path") or "").strip()
        if not path:
            raise ValueError("knowledge needs a path")
        if any(isinstance(k, dict) and k.get("path") == path
               for k in loadout["knowledge"]):
            raise ValueError(f"{path} is already attached")
        loadout["knowledge"].append(
            {"path": path, "teaches": str(body.get("teaches") or "").strip()})
        name = Path(path).name or path
    else:
        name = str(body.get("name") or "").strip().lstrip("/")
        if not name:
            raise ValueError("name is required")
        if name in [str(x).lstrip("/") for x in loadout[kind]]:
            raise ValueError(f"{name} is already equipped")
        if kind == "cli" and shutil.which(name.split()[0]) is None:
            # Same honesty contract as the pulse preflight (fork 014):
            # presence is the one thing we can assert about an uncataloged
            # tool. Probe the FIRST token so a base extension equipped as
            # `base <ext>` verifies the `base` binary, not the two-word string.
            raise ValueError(
                f"`{name.split()[0]}` did not resolve on this process's PATH — "
                "install it or check the name before equipping")
        if kind == "mcp":
            from firm.dashboard import discovery
            specs = discovery.raw_specs([name])
            if name not in specs:
                # a server that exists only in this firm's own .mcp.json is
                # already wired — loadout entry is all that's missing
                try:
                    own = json.loads(
                        (workspace / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers") or {}
                except (OSError, json.JSONDecodeError):
                    own = {}
                if not isinstance(own.get(name), dict):
                    raise ValueError(
                        f"no runnable spec found for {name!r} — "
                        "it is not in your MCP config or any enabled plugin")
            else:
                sysconfig_svc.mcp_set(conn, firm_id, workspace, name, specs[name])
                needs_keys = [
                    k for k, v in (specs[name].get("env") or {}).items()
                    if isinstance(v, str) and v.startswith("${")
                ]
        loadout[kind].append(name)

    repo.update(conn, "contract", contract["id"], {"skill_loadout": loadout})
    log_event(
        conn,
        firm_id=firm_id,
        event_type="member.equipped",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
        details={"kind": kind, "name": name},
    )
    return {"member_id": member_id, "kind": kind, "name": name,
            "needs_keys": needs_keys}


def unequip_member(
    conn: sqlite3.Connection,
    firm_id: str,
    member_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Remove one loadout entry. MCP unequip touches the loadout ONLY —
    the firm's ``.mcp.json`` is firm-wide armory another Member may share;
    pruning it is Train's call, not a socket click."""
    kind = str(body.get("kind") or "")
    if kind not in _EQUIP_KINDS:
        raise ValueError(f"unknown equip kind {kind!r}")
    contract, loadout = _member_loadout(conn, firm_id, member_id)
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")

    if kind == "knowledge":
        kept = [k for k in loadout["knowledge"]
                if not (isinstance(k, dict)
                        and (k.get("path") == name
                             or Path(str(k.get("path") or "")).name == name))]
        if len(kept) == len(loadout["knowledge"]):
            raise ValueError(f"{name} is not attached")
        loadout["knowledge"] = kept
    else:
        bare = name.lstrip("/")
        kept = [x for x in loadout[kind] if str(x).lstrip("/") != bare]
        if len(kept) == len(loadout[kind]):
            raise ValueError(f"{name} is not equipped")
        loadout[kind] = kept
        name = bare

    repo.update(conn, "contract", contract["id"], {"skill_loadout": loadout})
    log_event(
        conn,
        firm_id=firm_id,
        event_type="member.unequipped",
        actor={"type": "board", "id": None},
        target_ref={"type": "member", "id": member_id},
        details={"kind": kind, "name": name},
    )
    return {"member_id": member_id, "kind": kind, "name": name}


def _firm_post(
    h: BaseHTTPRequestHandler,
    workspace: Path,
    db_path: Path,
    firm_id: str,
    path: str,
) -> None:
    """POST routing for one firm's boardroom (firm-relative *path*)."""
    parts = path.strip("/").split("/")
    # Route: /api/inventory/sync — re-survey the machine into the Armory.
    # Machine-tier, not firm state — no Records entry, any firm may trigger it.
    if parts == ["api", "inventory", "sync"]:
        from firm.dashboard import inventory as inventory_mod
        inv = inventory_mod.sync()
        _http_send(h, 200, {"ok": True, "result": {
            "generated_at": inv.get("generated_at"),
            "counts": {k: len(inv.get(k) or []) for k in ("mcp", "skills", "commands", "cli")},
        }})
        return
    # Route: /api/sysconfig/… — platform config, vault vars, MCP, tools
    if parts[:2] == ["api", "sysconfig"]:
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            _http_send(h, 400, {"ok": False, "error": "invalid JSON body"})
            return
        _sysconfig_post(h, workspace, db_path, firm_id, path, body)
        return
    # Route: /api/extensions/install — drop-in extension package installer
    if parts == ["api", "extensions", "install"]:
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            _http_send(h, 400, {"ok": False, "error": "invalid JSON body"})
            return
        try:
            _http_send(h, 200, install_extension(
                workspace, body.get("package") or {}, bool(body.get("confirmed"))))
        except ValueError as exc:
            _http_send(h, 400, {"ok": False, "error": str(exc)})
        return
    # Routes: /api/views/<id>/action/<key> — firm-declared view actions
    if len(parts) == 5 and parts[:2] == ["api", "views"] and parts[3] == "action":
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            _http_send(h, 400, {"ok": False, "error": "invalid JSON body"})
            return
        views = {v["id"]: v for v in load_custom_views(workspace)}
        view = views.get(parts[2])
        try:
            if view is None:
                raise ValueError(f"unknown view {parts[2]!r}")
            _http_send(h, 200, run_view_action(workspace, view, parts[4], body))
        except ValueError as exc:
            _http_send(h, 404, {"ok": False, "error": str(exc)})
        return
    # Routes: /api/action/<action>/<entity_id>
    if len(parts) != 4 or parts[:2] != ["api", "action"]:
        _http_send(h, 404, {"error": "not found"})
        return
    _, _, action, entity_id = parts
    length = int(h.headers.get("Content-Length") or 0)
    raw = h.rfile.read(length) if length else b"{}"
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        _http_send(h, 400, {"error": "invalid JSON body"})
        return
    if action == "pulse":
        # Board's wake-the-firm button — no DB work here; the pulse
        # process owns all state changes and audit records. A member id in
        # the body (or as the entity) makes it a targeted single-member
        # pulse instead of waking every eligible member.
        only = body.get("member") or (
            entity_id if entity_id.startswith("MEM-") else None
        )
        try:
            _http_send(h, 200, {
                "ok": True,
                "result": _fire_pulse(workspace, firm_id, only=only),
            })
        except ValueError as exc:
            _http_send(h, 400, {"ok": False, "error": str(exc)})
        return
    if action == "member-commission":
        conn = connect(db_path)
        try:
            result = create_commission(conn, workspace, firm_id, entity_id, body)
            _http_send(h, 200, {"ok": True, "result": result})
        except ValueError as exc:
            _http_send(h, 400, {"ok": False, "error": str(exc)})
        finally:
            conn.close()
        return
    if action in ("member-equip", "member-unequip"):
        conn = connect(db_path)
        try:
            result = (
                equip_member(conn, workspace, firm_id, entity_id, body)
                if action == "member-equip"
                else unequip_member(conn, firm_id, entity_id, body)
            )
            _http_send(h, 200, {"ok": True, "result": result})
        except ValueError as exc:
            _http_send(h, 400, {"ok": False, "error": str(exc)})
        finally:
            conn.close()
        return
    if action == "escalation-resolve" and body.get("queue_followup"):
        # Resolve AND hand the resolution back as work: the Board's answer
        # becomes a claimed unit for the raiser plus a targeted dispatch —
        # the loop that keeps turn-based firms (The Table) moving.
        conn = connect(db_path)
        try:
            esc = repo.get(conn, "escalation", entity_id)
            if not esc:
                raise ValueError(f"unknown escalation {entity_id!r}")
            resolved = perform_action(conn, "escalation-resolve", entity_id, body)
            raiser = esc.get("raised_by_member_id")
            followup = None
            if raiser:
                followup = create_commission(conn, workspace, firm_id, raiser, {
                    "instructions": (
                        f"The Board resolved your escalation {entity_id} "
                        f"({esc.get('title')}). Resolution, verbatim: "
                        f"{body.get('resolution') or '(no note)'} — act on this "
                        "direction and continue."
                    ),
                })
            _http_send(h, 200, {"ok": True, "result": {
                "resolved": resolved, "followup": followup,
            }})
        except ValueError as exc:
            _http_send(h, 400, {"ok": False, "error": str(exc)})
        finally:
            conn.close()
        return
    conn = connect(db_path)
    try:
        if action == "member-instructions":
            result = write_instructions(
                conn, workspace, entity_id, body.get("content") or "",
            )
        else:
            result = perform_action(conn, action, entity_id, body)
        _http_send(h, 200, {"ok": True, "result": result})
    except ValueError as exc:
        _http_send(h, 400, {"ok": False, "error": str(exc)})
    finally:
        conn.close()


def make_handler(workspace: Path, firm_id: str) -> type[BaseHTTPRequestHandler]:
    db_path = get_db_path(workspace)

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CadreBoardroom/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            pass  # keep the terminal quiet; this is a local tool

        def do_GET(self) -> None:
            _firm_get(self, workspace, db_path, firm_id, self.path)

        def do_POST(self) -> None:
            _firm_post(self, workspace, db_path, firm_id, self.path)

    return DashboardHandler


def run_dashboard(
    workspace: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8484,
    firm_id: str | None = None,
) -> int:
    """Serve the Boardroom dashboard for *workspace*. Blocks until Ctrl-C."""
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_is_remote() and not db_path.exists():
        print(json.dumps({
            "ok": False, "reason": "db-not-found", "workspace": str(workspace),
        }))
        return 1

    # Older firms pick up new schema (e.g. escalation table) transparently.
    conn = connect(db_path)
    try:
        apply_migrations(conn)
        try:
            firm_id = resolve_firm_id(conn, firm_id)
        except ValueError as exc:
            print(json.dumps({"ok": False, "reason": "firm-id-unresolved",
                              "message": str(exc)}))
            return 1
    finally:
        conn.close()

    handler = make_handler(workspace, firm_id)
    server = ThreadingHTTPServer((host, port), handler)
    print(json.dumps({
        "ok": True,
        "url": f"http://{host}:{port}",
        "workspace": str(workspace),
        "firm_id": firm_id,
    }))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# Hub — one process, every firm (portfolio landing + /f/<firm>/ tenants)
# ---------------------------------------------------------------------------

_FIRM_PREFIX_RE = re.compile(r"^/f/([a-z0-9][a-z0-9_-]*)(/.*)?$")


def discover_firms(root: Path) -> dict[str, dict[str, Any]]:
    """Scan *root* for workspaces holding ``.firm/firm.db``.

    The firm id comes from each db's firm row (folder names are the
    operator's business; ids are the runtime's). New firm = new folder —
    no registration step.
    """
    firms: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return firms
    for d in sorted(root.iterdir()):
        db = d / ".firm" / "firm.db"
        if not db.exists():
            continue
        try:
            conn = connect(db)
            try:
                apply_migrations(conn)
                row = conn.execute("SELECT id, name FROM firm LIMIT 1").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            continue  # unreadable db — skip, don't take the hub down
        if not row:
            continue
        if row["id"] in firms:
            # Duplicate firm id (usually a backup copy left inside the scan
            # root). NEVER let it silently shadow the canonical workspace —
            # prefer the folder whose name matches the firm id, else first
            # wins; the loser is reported, not served.
            keep = firms[row["id"]]
            challenger = {"workspace": d.resolve(), "db_path": db.resolve(),
                          "name": row["name"] or row["id"]}
            if d.name == row["id"] and keep["workspace"].name != row["id"]:
                keep, challenger = challenger, keep
            firms[row["id"]] = keep
            firms[row["id"]].setdefault("shadowed", []).append(
                str(challenger["workspace"]))
            print(json.dumps({
                "warning": f"duplicate firm id {row['id']!r}",
                "serving": str(keep["workspace"]),
                "ignored": str(challenger["workspace"]),
                "hint": "move backup copies out of the hub's firms root",
            }))
            continue
        firms[row["id"]] = {
            "workspace": d.resolve(),
            "db_path": db.resolve(),
            "name": row["name"] or row["id"],
        }
    return firms


# --- Board prefs — floor order + the desk pad ------------------------------
# One JSON sidecar at the firms root, so the arrangement survives any browser
# and any device. Never firm state — never inside a firm's .firm/.
_PREFS_NAME = ".cadre-hub.json"


def load_prefs(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / _PREFS_NAME).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_prefs(root: Path, patch: dict[str, Any]) -> dict[str, Any]:
    prefs = load_prefs(root)
    prefs.update(patch)
    (root / _PREFS_NAME).write_text(
        json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def _floor_sort(cards: list[dict[str, Any]],
                order: list[str]) -> list[dict[str, Any]]:
    """Bottom-up: order[0] = floor 01. Firms the Board has never arranged
    stack above the arranged ones, oldest first — founding order, like a
    real building. Every consumer of the firm list (the boardroom stack,
    the firm-switcher dropdown) inherits this order from the payload."""
    rank = {fid: i for i, fid in enumerate(order)}
    cards.sort(key=lambda c: (0, rank[c["id"]]) if c["id"] in rank
               else (1, str(c.get("founded_at") or ""), c["id"]))
    for i, c in enumerate(cards):
        c["floor"] = i + 1
    return cards


def hub_summary(firms: dict[str, dict[str, Any]],
                prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Portfolio payload: one health card per firm."""
    now = datetime.now(tz=timezone.utc)
    cards = []
    for fid, info in firms.items():
        conn = connect(info["db_path"])
        try:
            members = repo.find(conn, "member", firm_id=fid)
            gates = [g for g in repo.find(conn, "gate", firm_id=fid)
                     if g.get("status") == "pending" and not g.get("dismissed_at")]
            escalations = [e for e in repo.find(conn, "escalation", firm_id=fid)
                           if e.get("status") in ("open", "acknowledged")]
            running = repo.find(conn, "member_run", firm_id=fid, status="running")
            stale = 0
            for r in running:
                try:
                    started = datetime.fromisoformat(r.get("started_at") or "")
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue
                deadline = 2 * _contract_timeout_sec(conn, r["member_id"]) + _REAP_GRACE_SEC
                if (now - started).total_seconds() > deadline:
                    stale += 1
            last_run = conn.execute(
                "SELECT MAX(started_at) FROM member_run WHERE firm_id = ?", (fid,),
            ).fetchone()[0]
            spend = conn.execute(
                "SELECT COALESCE(SUM(dollar_equivalent), 0) FROM usage_event "
                "WHERE firm_id = ?", (fid,),
            ).fetchone()[0]
            firm_row = repo.get(conn, "firm", fid) or {}
            founded = firm_row.get("created_at")
            schedule = firm_row.get("schedule")
        finally:
            conn.close()
        cards.append({
            "id": fid,
            "name": info["name"],
            "founded_at": founded,
            # A firm with no cadence cannot wake itself — that is "not
            # operational", a different state from "healthy and idle", and
            # the whole portfolio sat in the first while wearing the second
            # (fork 005). Manual-only Boards read it as a statement of fact.
            "schedule": schedule,
            "operational": bool(schedule),
            "workspace": str(info["workspace"]),
            "needs_you": len(gates) + len(escalations),
            "gates_pending": len(gates),
            "escalations_open": len(escalations),
            "running": len(running),
            "stale_runs": stale,
            "members_active": sum(1 for m in members if m.get("status") == "active"),
            "members_total": len(members),
            "spend_usd": round(float(spend or 0), 2),
            "last_run_at": last_run,
            "views": [{"id": v["id"], "title": v["title"]}
                      for v in load_custom_views(info["workspace"])],
        })
    _floor_sort(cards, (prefs or {}).get("floor_order") or [])
    return {"generated_at": now.isoformat(), "firms": cards}


_HUB_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cadre — Boardrooms</title>
<style>
:root{--canvas:#0f1211;--card:#161a18;--border:#262b28;--border-hi:#33403a;--text:#e8ebe9;
  --dim:#8a938e;--mono:#6b746f;--ok:#22c55e;--warn:#eab308;--gold:#d4a94a;--danger:#ef4444}
*{box-sizing:border-box;margin:0}
body{background:var(--canvas);color:var(--text);font:15px/1.5 system-ui,sans-serif;padding:40px 24px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:22px;font-weight:650;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin:4px 0 28px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.fcard{display:block;background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:20px;color:inherit;cursor:pointer;transition:border-color 160ms}
.fcard:hover{border-color:var(--border-hi)}
.fcard.attn{border-color:rgba(212,169,74,.5)}
.fcard.live{position:relative;overflow:hidden;border-color:rgba(34,197,94,.55);animation:cardglow 2.4s ease-in-out infinite}
.fcard.live:hover{border-color:rgba(34,197,94,.85)}
/* subtle green sheen sweeping across an active card — low alpha to avoid banding */
.fcard.live::before{content:"";position:absolute;inset:0;z-index:0;pointer-events:none;
  background:linear-gradient(115deg,transparent 0%,rgba(34,197,94,.04) 32%,rgba(52,211,153,.13) 50%,rgba(34,197,94,.04) 68%,transparent 100%);
  background-size:250% 100%;animation:shimmer 4.2s linear infinite}
.fcard.live>*{position:relative;z-index:1}
.fname{font-size:16px;font-weight:650;display:flex;align-items:center;gap:8px}
.dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
.dot.live{background:var(--ok);animation:pulse 1.8s ease-in-out infinite}
.dot.stale{background:var(--warn)}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.55)}70%{box-shadow:0 0 0 7px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
@keyframes cardglow{0%,100%{box-shadow:0 0 10px 0 rgba(34,197,94,.10)}50%{box-shadow:0 0 22px 2px rgba(34,197,94,.22)}}
@keyframes shimmer{0%{background-position:150% 0}100%{background-position:-150% 0}}
@media(prefers-reduced-motion:reduce){.dot.live,.fcard.live::before{animation:none}.fcard.live{animation:none;box-shadow:0 0 16px 1px rgba(34,197,94,.16)}}
.fid{font:500 10.5px ui-monospace,monospace;letter-spacing:.08em;color:var(--mono);text-transform:uppercase;margin-top:2px}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;margin-top:14px}
.stat .k{font:500 9.5px ui-monospace,monospace;letter-spacing:.1em;color:var(--mono);text-transform:uppercase}
.stat .v{font-size:14px;font-weight:600;margin-top:1px}
.v .warn{color:var(--warn)} .v.gold{color:var(--gold)} .v.ok{color:var(--ok)}
.views{margin-top:14px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:8px;flex-wrap:wrap}
.views a{font-size:12px;color:var(--ok);text-decoration:none;border:1px solid var(--border);
  border-radius:9999px;padding:3px 10px}
.views a:hover{border-color:var(--ok)}
.empty{color:var(--dim);padding:40px;text-align:center}
</style></head><body><div class="wrap">
<h1>Cadre — Boardrooms</h1>
<div class="sub" id="sub">Every firm, one door.</div>
<div class="grid" id="grid"><div class="empty">Loading…</div></div>
</div>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function ago(iso){
  if(!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if(s < 90) return Math.round(s) + 's ago';
  if(s < 5400) return Math.round(s/60) + 'm ago';
  if(s < 129600) return Math.round(s/3600) + 'h ago';
  return Math.round(s/86400) + 'd ago';
}
async function load(){
  try{
    const r = await fetch('/api/hub');
    const data = await r.json();
    const firms = data.firms || [];
    const activeN = firms.filter(f => f.running > f.stale_runs).length;
    document.getElementById('sub').textContent =
      firms.length + ' firm' + (firms.length === 1 ? '' : 's') + ' · one door'
      + (activeN ? ' · ' + activeN + ' active' : '') + '.';
    document.getElementById('grid').innerHTML = firms.length ? firms.map(f => `
      <div class="fcard${f.needs_you ? ' attn' : ''}${f.running > f.stale_runs ? ' live' : ''}" role="link" tabindex="0"
        onclick="location.href='/f/${esc(f.id)}/'"
        onkeydown="if(event.key==='Enter')location.href='/f/${esc(f.id)}/'">
        <div class="fname">${f.running ? `<span class="dot ${f.running > f.stale_runs ? 'live' : 'stale'}" title="${f.running > f.stale_runs ? 'Active — '+f.running+' running' : f.stale_runs+' stale run(s)'}"></span>` : ''}${esc(f.name)}</div>
        <div class="fid">${esc(f.id)}</div>
        <div class="stats">
          <div class="stat"><div class="k">Needs you</div>
            <div class="v${f.needs_you ? ' gold' : ''}">${f.needs_you}</div></div>
          <div class="stat"><div class="k">Running</div>
            <div class="v${f.running && !f.stale_runs ? ' ok' : ''}">${f.running}${f.stale_runs ? ` <span class="warn">(${f.stale_runs} stale)</span>` : ''}</div></div>
          <div class="stat"><div class="k">Members</div>
            <div class="v">${f.members_active}/${f.members_total}</div></div>
          <div class="stat"><div class="k">Spend</div>
            <div class="v">$${(f.spend_usd || 0).toFixed(2)}</div></div>
          <div class="stat"><div class="k">Last run</div>
            <div class="v">${esc(ago(f.last_run_at))}</div></div>
        </div>
        ${f.views && f.views.length ? `<div class="views">${f.views.map(v =>
          `<a href="/f/${esc(f.id)}/view/${esc(v.id)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(v.title)} ↗</a>`).join('')}</div>` : ''}
      </div>`).join('') : '<div class="empty">No firms found.</div>';
  }catch(e){}
}
load(); setInterval(load, 10000);
</script></body></html>"""


def make_hub_handler(root: Path) -> type[BaseHTTPRequestHandler]:
    registry: dict[str, dict[str, Any]] = discover_firms(root)

    def _resolve(fid: str) -> dict[str, Any] | None:
        if fid not in registry:
            registry.clear()
            registry.update(discover_firms(root))  # lazy rescan — new firms appear live
        return registry.get(fid)

    class HubHandler(BaseHTTPRequestHandler):
        server_version = "CadreHub/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            pass  # local tool — keep the terminal quiet

        def do_GET(self) -> None:
            path = self.path
            if path in ("/", "/index.html"):
                _http_send(self, 200, _HUB_HTML.encode(), "text/html; charset=utf-8")
                return
            if path == "/api/hub":
                registry.clear()
                registry.update(discover_firms(root))
                _http_send(self, 200, hub_summary(registry, load_prefs(root)))
                return
            if path in ("/next", "/next/", "/next/index.html"):
                _http_send(self, 200, _NEXT_HTML.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/api/next/hub":
                from firm.dashboard import boardroom
                registry.clear()
                registry.update(discover_firms(root))
                prefs = load_prefs(root)
                summary = hub_summary(registry, prefs)
                summary["firms"] = boardroom.enrich(summary["firms"], registry)
                summary["prefs"] = prefs
                _http_send(self, 200, summary)
                return
            if path.startswith("/api/next/found/"):
                from firm.dashboard import founding
                route, _, qs = path.partition("?")
                job_id = route.rsplit("/", 1)[-1]
                cursor = 0
                for kv in qs.split("&"):
                    if kv.startswith("cursor="):
                        try:
                            cursor = max(0, int(kv.split("=", 1)[1]))
                        except ValueError:
                            cursor = 0
                _http_send(self, 200, founding.status(job_id, cursor))
                return
            if path.startswith("/api/next/readiness/"):
                from firm.dashboard import founding
                fid = path.rsplit("/", 1)[-1].split("?")[0]
                if fid not in registry:
                    _http_send(self, 404, {"ok": False, "error": "not found"})
                    return
                _http_send(self, 200, founding.readiness(root, fid))
                return
            if path == "/api/next/coboard":
                from firm.dashboard import coboard
                _http_send(self, 200, {"ok": True,
                                       "briefed": coboard.briefed_firms(root)})
                return
            if path == "/api/next/notify-presets":
                from firm.dashboard import discovery
                _http_send(self, 200, {"ok": True,
                                       "presets": discovery.notify_presets(root)})
                return
            if path == "/api/next/hub-extensions":
                # Board-level (framework) extensions — links the hub renders
                # generically. Firm plugins are a different door (views.json).
                from firm.dashboard import hub_extensions
                _http_send(self, 200, {"ok": True,
                                       "extensions": hub_extensions.load_all()})
                return
            if path == "/api/next/rail":
                # The rails' read seam (status_payload). The rails are Cadre
                # OS addons — an absent addon reports configured: False and
                # every rail-aware UI element self-hides.
                payload: dict[str, Any] = {"ok": True}
                for key, module in (("chat", "cadre_chat.cli"),
                                    ("slack", "cadre_slack.cli")):
                    try:
                        import importlib
                        payload[key] = importlib.import_module(module).status_payload()
                    except ImportError:
                        payload[key] = {"ok": True, "configured": False}
                _http_send(self, 200, payload)
                return
            if path.startswith("/api/next/pulse-state/"):
                from firm.dashboard import founding
                fid = path.rsplit("/", 1)[-1].split("?")[0]
                if fid not in registry:
                    _http_send(self, 404, {"ok": False, "error": "not found"})
                    return
                _http_send(self, 200, founding.pulse_state(fid))
                return
            if path.startswith("/api/next/brief/"):
                from firm.dashboard import coboard
                route, _, qs = path.partition("?")
                job_id = route.rsplit("/", 1)[-1]
                cursor = 0
                for kv in qs.split("&"):
                    if kv.startswith("cursor="):
                        try:
                            cursor = max(0, int(kv.split("=", 1)[1]))
                        except ValueError:
                            cursor = 0
                _http_send(self, 200, coboard.status(job_id, cursor))
                return
            if path.startswith("/api/next/wire/"):
                from firm.dashboard import wiring
                route, _, qs = path.partition("?")
                job_id = route.rsplit("/", 1)[-1]
                cursor = 0
                for kv in qs.split("&"):
                    if kv.startswith("cursor="):
                        try:
                            cursor = max(0, int(kv.split("=", 1)[1]))
                        except ValueError:
                            cursor = 0
                _http_send(self, 200, wiring.status(job_id, cursor))
                return
            if path.startswith("/api/next/survey/"):
                from firm.dashboard import discovery, exclusions
                fid = path.rsplit("/", 1)[-1].split("?")[0]
                info = _resolve(fid)
                if info is None:
                    _http_send(self, 404, {"ok": False, "error": "not found"})
                    return
                payload = discovery.survey(info["workspace"])
                payload["exclusions"] = exclusions.load()
                _http_send(self, 200, payload)
                return
            if path.startswith("/api/next/browse"):
                from firm.dashboard import discovery
                _, _, qs = path.partition("?")
                target = None
                for kv in qs.split("&"):
                    if kv.startswith("path="):
                        from urllib.parse import unquote
                        target = unquote(kv.split("=", 1)[1])
                try:
                    _http_send(self, 200, discovery.browse(target))
                except ValueError as exc:
                    _http_send(self, 400, {"ok": False, "error": str(exc)})
                return
            m = _FIRM_PREFIX_RE.match(path)
            if m:
                fid, sub = m.group(1), m.group(2) or ""
                info = _resolve(fid)
                if info is None:
                    _http_send(self, 404, {"error": f"unknown firm {fid!r}"})
                    return
                if not sub:  # /f/<firm> → /f/<firm>/ so relative URLs resolve
                    self.send_response(302)
                    self.send_header("Location", f"/f/{fid}/")
                    self.end_headers()
                    return
                _firm_get(self, info["workspace"], info["db_path"], fid, sub,
                          base=f"/f/{fid}")
                return
            _http_send(self, 404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.startswith("/api/next/"):
                from firm.dashboard import founding
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    _http_send(self, 400, {"ok": False, "error": "invalid JSON body"})
                    return
                verb = self.path.rsplit("/", 1)[-1]
                if verb == "prefs":
                    patch: dict[str, Any] = {}
                    if isinstance(body.get("floor_order"), list):
                        patch["floor_order"] = [str(x) for x in body["floor_order"]][:200]
                    if isinstance(body.get("notes"), str):
                        patch["notes"] = body["notes"][:20000]
                    if not patch:
                        _http_send(self, 400, {"ok": False, "error": "nothing to save"})
                        return
                    _http_send(self, 200, {"ok": True, "prefs": save_prefs(root, patch)})
                elif verb == "hub-extensions":
                    from firm.dashboard import hub_extensions
                    if body.get("remove"):
                        removed = hub_extensions.remove(str(body["remove"]))
                        _http_send(self, 200 if removed else 404,
                                   {"ok": removed,
                                    "extensions": hub_extensions.load_all()}
                                   if removed else
                                   {"ok": False, "error": "not installed"})
                        return
                    entry, why = hub_extensions.validate(body.get("package"))
                    if entry is None:
                        _http_send(self, 400, {"ok": False, "error": why})
                        return
                    hub_extensions.save(entry)
                    _http_send(self, 200, {"ok": True, "installed": entry,
                                           "extensions": hub_extensions.load_all()})
                elif verb == "rail":
                    # The rails' write seam (apply_setting) — one option per
                    # call, service bounced by the seam itself.
                    modname = {"chat": "cadre_chat.cli",
                               "slack": "cadre_slack.cli"}.get(
                        str(body.get("rail") or ""))
                    if modname is None:
                        _http_send(self, 400,
                                   {"ok": False, "error": "rail must be chat or slack"})
                        return
                    try:
                        import importlib
                        mod = importlib.import_module(modname)
                    except ImportError:
                        _http_send(self, 400, {"ok": False,
                                   "error": "that rail addon is not installed"})
                        return
                    _http_send(self, 200, mod.apply_setting(
                        str(body.get("key") or ""), body.get("value")))
                elif verb == "found":
                    _http_send(self, 200, founding.start(body.get("brief") or ""))
                elif verb == "reshuffle":
                    _http_send(self, 200, founding.reshuffle(
                        body.get("proposal") or {}, str(body.get("note") or "")))
                elif verb == "cancel":
                    _http_send(self, 200, founding.cancel(body.get("job_id") or ""))
                elif verb == "commit":
                    result = founding.commit(root, body.get("proposal") or {})
                    if result.get("ok"):
                        registry.clear()   # the new floor appears without a restart
                        registry.update(discover_firms(root))
                    _http_send(self, 200, result)
                elif verb == "pulse":
                    fid = str(body.get("firm_id") or "")
                    if fid not in registry:
                        _http_send(self, 404, {"ok": False, "error": "not found"})
                        return
                    _http_send(self, 200, founding.set_pulse(
                        root, fid,
                        str(body.get("interval") or "30m"),
                        enable=bool(body.get("enable", True))))
                elif verb in ("brief", "brief-commit"):
                    from firm.dashboard import coboard
                    fid = str(body.get("firm_id") or "")
                    info = _resolve(fid)
                    if info is None:
                        _http_send(self, 404, {"ok": False, "error": "not found"})
                        return
                    if verb == "brief":
                        _http_send(self, 200, coboard.start(info["workspace"], fid, {
                            "cadence": body.get("cadence"),
                            "channel": body.get("channel"),
                            "voice": body.get("voice") or "",
                            "gaps": body.get("gaps") or [],
                        }))
                    else:
                        _http_send(self, 200, coboard.commit(
                            root, fid, str(body.get("brief") or "")))
                elif verb == "manifest":
                    fid = str(body.get("firm_id") or "")
                    if fid not in registry:
                        _http_send(self, 404, {"ok": False, "error": "not found"})
                        return
                    _http_send(self, 200, founding.set_manifest(
                        root, fid, body.get("manifest") or {}))
                elif verb in ("wire", "wire-commit"):
                    from firm.dashboard import wiring
                    fid = str(body.get("firm_id") or "")
                    info = _resolve(fid)
                    if info is None:
                        _http_send(self, 404, {"ok": False, "error": "not found"})
                        return
                    if verb == "wire":
                        _http_send(self, 200, wiring.start(info["workspace"], fid, {
                            "mcp": body.get("mcp") or [],
                            "folders": body.get("folders") or [],
                            "voice": body.get("voice") or "",
                        }))
                    else:
                        _http_send(self, 200, wiring.commit(
                            root, fid, body.get("plan") or {}, body.get("keys") or {}))
                elif verb == "exclude":
                    from firm.dashboard import exclusions
                    try:
                        data = exclusions.toggle(
                            str(body.get("kind") or ""),
                            str(body.get("name") or ""),
                            bool(body.get("excluded", True)))
                    except ValueError as exc:
                        _http_send(self, 400, {"ok": False, "error": str(exc)})
                        return
                    _http_send(self, 200, {"ok": True, "exclusions": data})
                elif verb == "launch":
                    from firm.dashboard import launch
                    fid = str(body.get("firm_id") or "")
                    if _resolve(fid) is None:
                        _http_send(self, 404, {"ok": False, "error": "not found"})
                        return
                    # cwd is the firms root, not the firm — the Co-Board is an
                    # overseer, not a member; /boardroom <fid> directs it in.
                    _http_send(self, 200, launch.summon(
                        str(root), fid, str(body.get("agenda") or "")))
                else:
                    _http_send(self, 404, {"ok": False, "error": "not found"})
                return
            m = _FIRM_PREFIX_RE.match(self.path)
            if not m:
                _http_send(self, 404, {"error": "not found"})
                return
            fid, sub = m.group(1), m.group(2) or "/"
            info = _resolve(fid)
            if info is None:
                _http_send(self, 404, {"error": f"unknown firm {fid!r}"})
                return
            _firm_post(self, info["workspace"], info["db_path"], fid, sub)

    return HubHandler


def run_hub(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8484,
) -> int:
    """Serve every firm under *root* from one process. Blocks until Ctrl-C.

    The hub is multi-firm by definition, so the single-firm CADRE_DB_URL
    override is actively stripped here — inheriting it (e.g. from a shell
    that sourced a firm's .env) would silently point EVERY firm's card at
    one shared database. Remote-backed firms get their own dedicated
    ``cadre dashboard`` process with the env set."""
    dropped = [k for k in ("CADRE_DB_URL", "CADRE_DB_TOKEN") if os.environ.pop(k, None)]
    if dropped:
        print(json.dumps({"warning": "hub ignores " + "/".join(dropped)
                          + " — remote-backed firms need their own dashboard process"}))
    root = root.expanduser().resolve()
    firms = discover_firms(root)
    if not firms:
        print(json.dumps({"ok": False, "reason": "no-firms-found", "root": str(root)}))
        return 1
    from firm.dashboard import launch
    launch.ensure_boardroom_claude(root)   # the Co-Board's loadout, laid once
    handler = make_hub_handler(root)
    server = ThreadingHTTPServer((host, port), handler)
    print(json.dumps({
        "ok": True,
        "url": f"http://{host}:{port}",
        "root": str(root),
        "firms": sorted(firms),
    }))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
