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
import re
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.pulse.orchestrator import compute_load
from firm.services import comment as comment_svc
from firm.services import document as document_svc
from firm.services import escalation as escalation_svc
from firm.services import gate as gate_svc
from firm.services import goal as goal_svc
from firm.services import member as member_svc
from firm.services import unit as unit_svc
from firm.services._records import log_event

_INDEX_HTML = Path(__file__).parent / "index.html"

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
        views.append({
            "id": vid,
            "title": str(v.get("title") or vid),
            "fragment": str(v["fragment"]),
            "files": {str(k): str(p) for k, p in files.items()},
            "dirs": {str(k): str(p) for k, p in dirs.items()},
        })
    return views


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


_VIEW_PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>html,body{margin:0;padding:0;min-height:100%;background:#141109}
#viewRoot{padding:24px}</style>
</head><body>
<div id="viewRoot"></div>
<script>
/* Minimal CadreShell bridge — same contract the boardroom shell exposes,
   so a fragment renders identically full-page and embedded. */
window.CadreShell = {
  _state: {},
  state: function(){ return this._state; },
  post: async function(path, body){
    try{
      const r = await fetch('/api/action/' + path, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {}),
      });
      return await r.json();
    }catch(e){ return {ok: false, error: String(e)}; }
  },
  viewFile: async function(viewId, key){
    const r = await fetch('/api/views/' + viewId + '/file/' + key);
    if(!r.ok) throw new Error(key + ' ' + r.status);
    const ct = r.headers.get('Content-Type') || '';
    return ct.includes('json') ? r.json() : r.text();
  },
};
async function __poll(){
  try{
    const r = await fetch('/api/state');
    CadreShell._state = await r.json();
    document.dispatchEvent(new CustomEvent('cadre:state', {detail: CadreShell._state}));
  }catch(e){}
}
try{
  const es = new EventSource('/api/events');
  es.addEventListener('change', __poll);
}catch(e){}
setInterval(__poll, 15000);
__poll().then(async function(){
  const r = await fetch('/api/views/__VIEW_ID__/fragment');
  const root = document.getElementById('viewRoot');
  root.innerHTML = await r.text();
  root.querySelectorAll('script').forEach(function(old){
    const s = document.createElement('script');
    s.textContent = old.textContent;
    old.replaceWith(s);
  });
});
</script></body></html>"""


def render_view_page(view: dict[str, Any]) -> bytes:
    """Full-page wrapper for a custom view — the fragment as its own app.

    Same fragment, same CadreShell contract as the boardroom shell; no
    boardroom chrome. Served at ``/view/<id>``.
    """
    return (
        _VIEW_PAGE_TEMPLATE
        .replace("__TITLE__", view["title"])
        .replace("__VIEW_ID__", view["id"])
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
    return (e - s).total_seconds()


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
    for r in runs:
        r["duration_sec"] = _run_duration_sec(r)
        r["cost_usd"] = run_costs.get(r["id"])
        r.pop("prompt_snapshot", None)

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

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "firm": firm,
        "roster": roster,
        "operations": operations,
        "projects": projects,
        "units": units,
        "gates": gates,
        "escalations": escalations,
        "goals": goals,
        "documents": documents,
        "runs": runs,
        "records": records,
        "comments": comments,
        "cost_by_member": cost_by_member,
        "budget_periods": budget_periods,
        "notify_configured": bool(firm.get("notify_config")),
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
        if data.get("name") is None or data.get("project_id") is None:
            raise ValueError("name and project_id are required")
        return unit_svc.create_unit(conn, firm_id_of(conn, body), data)
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

def make_handler(workspace: Path, firm_id: str) -> type[BaseHTTPRequestHandler]:
    db_path = get_db_path(workspace)

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CadreBoardroom/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            pass  # keep the terminal quiet; this is a local tool

        def _send(self, status: int, payload: dict | bytes, content_type: str = "application/json") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(200, _INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
                return
            if self.path == "/api/events":
                self._stream_events()
                return
            if self.path == "/api/state":
                conn = connect(db_path)
                try:
                    self._send(200, assemble_state(conn, firm_id))
                finally:
                    conn.close()
                return
            if self.path.startswith("/view/"):
                vid = self.path.strip("/").split("/")[1].split("?")[0]
                views = {v["id"]: v for v in load_custom_views(workspace)}
                if vid in views:
                    self._send(200, render_view_page(views[vid]), "text/html; charset=utf-8")
                else:
                    self._send(404, {"error": f"unknown view {vid!r}"})
                return
            if self.path == "/api/views":
                views = load_custom_views(workspace)
                self._send(200, {"views": [
                    {"id": v["id"], "title": v["title"], "files": sorted(v["files"])}
                    for v in views
                ]})
                return
            if self.path.startswith("/api/views/"):
                parts = self.path.strip("/").split("/")
                # /api/views/<id>/fragment  |  /api/views/<id>/file/<key>
                views = {v["id"]: v for v in load_custom_views(workspace)}
                view = views.get(parts[2]) if len(parts) >= 4 else None
                try:
                    if view is None:
                        raise ValueError("unknown view")
                    if parts[3] == "fragment" and len(parts) == 4:
                        self._send(200, read_view_fragment(workspace, view),
                                   "text/html; charset=utf-8")
                    elif parts[3] == "file" and len(parts) == 5:
                        content, ctype = read_view_file(workspace, view, parts[4])
                        self._send(200, content, ctype)
                    elif parts[3] == "dir" and len(parts) == 6:
                        content, ctype = read_view_dir_file(
                            workspace, view, parts[4], parts[5])
                        self._send(200, content, ctype)
                    else:
                        raise ValueError("unknown view route")
                except ValueError as exc:
                    self._send(404, {"error": str(exc)})
                return
            if self.path.startswith("/api/member/"):
                member_id = self.path.rsplit("/", 1)[1]
                conn = connect(db_path)
                try:
                    self._send(200, member_profile(conn, workspace, member_id))
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
                finally:
                    conn.close()
                return
            if self.path.startswith("/api/doc/"):
                doc_id = self.path.rsplit("/", 1)[1]
                conn = connect(db_path)
                try:
                    self._send(200, read_document(conn, workspace, doc_id))
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
                finally:
                    conn.close()
                return
            self._send(404, {"error": "not found"})

        def _stream_events(self) -> None:
            """SSE push: watch SQLite data_version (bumped by any other
            connection's commit) and tell the client the moment the firm
            changes — escalations, gates, runs land sub-second instead of
            on the poll cadence. Stdlib-only; EventSource auto-reconnects."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            conn = connect(db_path)
            try:
                last = conn.execute("PRAGMA data_version").fetchone()[0]
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                ticks = 0
                while True:
                    time.sleep(0.5)
                    ticks += 1
                    cur = conn.execute("PRAGMA data_version").fetchone()[0]
                    if cur != last:
                        last = cur
                        self.wfile.write(b"event: change\ndata: {}\n\n")
                        self.wfile.flush()
                    elif ticks % 30 == 0:
                        self.wfile.write(b": ping\n\n")  # keep-alive
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # client went away — thread ends
            finally:
                conn.close()

        def do_POST(self) -> None:
            # Routes: /api/action/<action>/<entity_id>
            parts = self.path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "action"]:
                self._send(404, {"error": "not found"})
                return
            _, _, action, entity_id = parts
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid JSON body"})
                return
            conn = connect(db_path)
            try:
                if action == "member-instructions":
                    result = write_instructions(
                        conn, workspace, entity_id, body.get("content") or "",
                    )
                else:
                    result = perform_action(conn, action, entity_id, body)
                self._send(200, {"ok": True, "result": result})
            except ValueError as exc:
                self._send(400, {"ok": False, "error": str(exc)})
            finally:
                conn.close()

    return DashboardHandler


def run_dashboard(
    workspace: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8484,
    firm_id: str = "chrisai",
) -> int:
    """Serve the Boardroom dashboard for *workspace*. Blocks until Ctrl-C."""
    workspace = workspace.expanduser().resolve()
    db_path = get_db_path(workspace)
    if not db_path.exists():
        print(json.dumps({
            "ok": False, "reason": "db-not-found", "workspace": str(workspace),
        }))
        return 1

    # Older firms pick up new schema (e.g. escalation table) transparently.
    conn = connect(db_path)
    try:
        apply_migrations(conn)
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
