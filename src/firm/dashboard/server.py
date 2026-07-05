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
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.pulse.orchestrator import compute_load
from firm.services import escalation as escalation_svc
from firm.services import gate as gate_svc
from firm.services import goal as goal_svc

_INDEX_HTML = Path(__file__).parent / "index.html"


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
    goals = repo.find(conn, "goal", firm_id=firm_id)
    documents = repo.find(conn, "document", firm_id=firm_id)

    runs = sorted(
        repo.find(conn, "member_run", firm_id=firm_id),
        key=lambda r: r.get("started_at") or "",
        reverse=True,
    )[:30]
    for r in runs:
        r["duration_sec"] = _run_duration_sec(r)
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
    raise ValueError(f"Unknown action {action!r}")


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
            if self.path == "/api/state":
                conn = connect(db_path)
                try:
                    self._send(200, assemble_state(conn, firm_id))
                finally:
                    conn.close()
                return
            self._send(404, {"error": "not found"})

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
