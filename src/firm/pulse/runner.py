"""PULSE runner — the orchestrator callback that chains all subsystems.

Implements the ``RunMemberFn`` contract: ``(conn, member_dict) -> result_dict``.
Chains: budget check → unit lookup → resolve runtime → member_run create →
Contract invoke → parse → validate → retry → budget update → finalize.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm.contracts.claude_code import ClaudeCodeRuntime
from firm.contracts.registry import resolve_runtime
from firm.core import repo
from firm.pulse.budget import (
    check_budget_preflight,
    check_rate_limit,
    update_budget_postrun,
)
from firm.pulse.parser import parse_stream
from firm.pulse.spawn import expected_mcp_servers, spawn_member_run
from firm.pulse.validate import retry_on_failure, validate_output
from firm.services._id import next_id
from firm.services.authority import system_context
from firm.services.document import _next_version_path, create_document, update_document
from firm.services.unit import complete_unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_contract(
    conn: sqlite3.Connection, member: dict[str, Any],
) -> dict[str, Any] | None:
    """Get the member's contract row, or None."""
    contract_id = member.get("contract_id")
    if not contract_id:
        return None
    return repo.get(conn, "contract", contract_id)


def _wants_deliverable_registration(
    validation_config: dict[str, Any] | None,
) -> bool:
    """True when the contract declares its units produce a file deliverable
    (a ``file_exists`` validator with ``require_written``). That same opt-in
    that makes a fileless run FAIL also means: when the run DOES write a file,
    the harness should register it as a Document so the Board can review it.
    """
    if isinstance(validation_config, str):
        try:
            validation_config = json.loads(validation_config)
        except (json.JSONDecodeError, TypeError):
            return False
    if not isinstance(validation_config, dict):
        return False
    for entry in validation_config.get("validators", []):
        if isinstance(entry, dict) and entry.get("name") == "file_exists" \
                and entry.get("require_written"):
            return True
    return False


def _register_deliverables(
    conn: sqlite3.Connection,
    firm_id: str,
    unit: dict[str, Any],
    member_id: str,
    parsed: dict[str, Any],
    validation_config: dict[str, Any] | None,
    cwd: str,
) -> None:
    """Register files a run wrote as Documents parented to the unit, so a
    completed unit's deliverable actually lands in the Board's review surface.

    Seam-4 owns completion; a completed unit with a verified file but no
    Document row is invisible to the Board (wastelander UNIT-023 / ch18 pilot,
    2026-07-07). Gated on the deliverable opt-in so scratch-writing members
    don't spam Documents; idempotent by content_path so retries don't dupe.
    """
    if not _wants_deliverable_registration(validation_config):
        return
    seen: set[str] = set()
    for tc in parsed.get("tool_calls") or []:
        if not isinstance(tc, dict) or tc.get("name") not in ("Write", "Edit"):
            continue
        inp = tc.get("input")
        fp = inp.get("file_path") if isinstance(inp, dict) else None
        if not fp or fp in seen:
            continue
        seen.add(fp)
        if not os.path.exists(fp):
            continue
        rel = os.path.relpath(fp, cwd) if cwd else fp
        if repo.find(conn, "document", firm_id=firm_id, content_path=rel):
            continue  # already registered
        # A revision writes foo-v2.md beside foo.md (never-overwrite). That is a new
        # version of the existing Document, not a sibling of it — registering it as a
        # fresh DOC row forks the version history and the Board loses the diff.
        prior = next(
            (
                d for d in repo.find(conn, "document", firm_id=firm_id)
                if _next_version_path(d.get("content_path") or "", d.get("version") or 1) == rel
            ),
            None,
        )
        if prior:
            update_document(
                conn,
                prior["id"],
                {"content_path": rel},
                actor={"type": "member", "id": member_id},
            )
            continue
        create_document(conn, firm_id, {
            "name": os.path.basename(fp),
            "type": "draft",
            "content_path": rel,
            "parent_entity_type": "unit",
            "parent_entity_id": unit["id"],
            "author_type": "member",
            "author_id": member_id,
        })


def _persist_final_text(
    conn: sqlite3.Connection, run_id: str, parsed: dict[str, Any],
) -> None:
    """Land the run's final message text on its row (``member_run.outputs``).

    A deliverable must never exist ONLY as un-persisted process stdout:
    wastelander RUN-051 (2026-07-10) "completed" a $4.09 canon check whose
    entire product was 1,853 chars of final text — outputs stayed NULL and
    the text evaporated when the pulse process exited. The harness persists
    what the model returned at every terminal transition, unconditionally.
    """
    text = parsed.get("text") or ""
    if not text:
        return
    repo.update(conn, "member_run", run_id, {
        "outputs": json.dumps([{"type": "final_text", "text": text}]),
    })


_MCP_LOG_ROOT = os.path.expanduser("~/.cache/claude-cli-nodejs")


def _mcp_log_verdict(cwd: str, session_id: str | None, server: str) -> bool | None:
    """Authoritative post-run connect check from claude's own MCP debug logs.

    claude writes per-project, per-server connection logs to
    ``~/.cache/claude-cli-nodejs/<cwd with / → ->/mcp-logs-<server>/*.jsonl``;
    every entry carries the stream ``sessionId``, so the verdict is precise
    to THIS run even with concurrent sessions in the same workspace.

    Returns True when this session logged a successful connect, False when
    this session's entries exist but never logged one (affirmative failure),
    None when no evidence is findable (log dir absent, session unknown, cache
    layout changed) — callers must treat None as "cannot assert", never flag.
    """
    if not cwd or not session_id:
        return None
    log_dir = os.path.join(
        _MCP_LOG_ROOT, cwd.replace("/", "-"), f"mcp-logs-{server}",
    )
    try:
        names = sorted(os.listdir(log_dir), reverse=True)  # ISO names: newest first
    except OSError:
        return None
    session_seen = False
    for name in names[:20]:  # this run's log was just written; bound the scan
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(log_dir, name), encoding="utf-8",
                      errors="replace") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict) \
                            or entry.get("sessionId") != session_id:
                        continue
                    session_seen = True
                    if "Successfully connected" in str(entry.get("debug", "")):
                        return True
        except OSError:
            continue
    return False if session_seen else None


def _mcp_startup_guard(
    conn: sqlite3.Connection,
    run_id: str,
    parsed: dict[str, Any],
    cwd: str,
) -> list[str]:
    """Flag a run whose expected firm MCP toolset provably never became
    reachable — visibly on the run row, instead of silently "completing"
    (ESC-004 / RUN-051: runs closed clean while ``mcp__firm__*`` was absent).

    Evidence model (RUN-053/054/055 postmortem, 2026-07-10): the init event
    is a snapshot taken BEFORE MCP connections settle — under systemd-run
    pulse timing the firm server reliably shows ``pending`` at init and
    connects ~500ms later, tools fully usable. Init status alone therefore
    CANNOT condemn a run. A server is healthy on any one of:

    1. init status ``connected``, or its tools in the init tool index;
    2. an ``mcp__<server>__*`` tool call observed anywhere in the stream;
    3. claude's per-session MCP debug log recording a successful connect.

    It is flagged missing only on affirmative failure with none of the above:
    init status failed/needs-auth/absent-from-init, or this session's own log
    entries exist without a connect. ``pending`` with no consultable log is
    indeterminate and stays silent — the guard must never false-flag healthy
    runs or the Board learns to ignore it.

    Returns the missing server names; [] = healthy or nothing to assert.
    """
    expected = expected_mcp_servers(cwd)
    if not expected:
        return []
    init_tools = parsed.get("init_tools")
    mcp_servers = parsed.get("mcp_servers")
    if init_tools is None and mcp_servers is None:
        return []
    statuses = {
        s.get("name"): s.get("status") for s in (mcp_servers or [])
    }
    session_id = parsed.get("session_id")
    missing: list[str] = []
    evidence: dict[str, str] = {}
    for name in expected:
        status = statuses.get(name)
        prefix = f"mcp__{name}__"
        if status == "connected":
            continue
        if any(t.startswith(prefix) for t in (init_tools or [])):
            continue
        if any(
            isinstance(tc, dict) and str(tc.get("name") or "").startswith(prefix)
            for tc in parsed.get("tool_calls") or []
        ):
            continue
        log_verdict = _mcp_log_verdict(cwd, session_id, name)
        if log_verdict is True:
            continue
        if status in ("failed", "needs-auth") or name not in statuses \
                or log_verdict is False:
            missing.append(name)
            evidence[name] = (
                f"init={status if name in statuses else 'absent'}, "
                f"log={'no-connect' if log_verdict is False else 'unavailable'}"
            )
        # status "pending" with no log verdict → indeterminate: stay silent
    if missing:
        repo.update(conn, "member_run", run_id, {
            "notes": json.dumps({
                "warning": "mcp_degraded",
                "missing_mcp_servers": missing,
                "server_status": {n: statuses.get(n) for n in missing},
                "evidence": evidence,
                "detail": (
                    "expected MCP servers from .mcp.json show no evidence of "
                    "connecting in this run — the Member worked without its "
                    "firm tools"
                ),
            }),
        })
    return missing


def _get_validation_config(
    contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract validation_config from a contract, handling JSON strings."""
    if not contract:
        return None
    vc = contract.get("validation_config")
    if isinstance(vc, str):
        try:
            vc = json.loads(vc)
        except (json.JSONDecodeError, TypeError):
            return None
    return vc if isinstance(vc, dict) else None


def _get_pulse_config_value(
    contract: dict[str, Any] | None, key: str, default: Any = None,
) -> Any:
    """Read a single value from contract.pulse_config."""
    if not contract:
        return default
    pc = contract.get("pulse_config")
    if isinstance(pc, str):
        try:
            pc = json.loads(pc)
        except (json.JSONDecodeError, TypeError):
            return default
    if isinstance(pc, dict):
        return pc.get(key, default)
    return default


def _find_claimed_unit(
    conn: sqlite3.Connection, member_id: str,
) -> dict[str, Any] | None:
    """Find first pending/in_progress unit claimed by member."""
    units = repo.find(conn, "unit", claimed_by=member_id)
    for u in units:
        if u.get("status") in ("pending", "in_progress"):
            return u
    return None


def _deps_met(conn: sqlite3.Connection, unit: dict[str, Any]) -> bool:
    """Hard dependency gate: every depends_on unit must be status=done.

    Until now depends_on was only rendered into the prompt — never enforced
    in the pulse path, so a Member could be dispatched onto a blocked Unit.
    """
    deps = unit.get("depends_on")
    if isinstance(deps, str):
        try:
            deps = json.loads(deps)
        except (json.JSONDecodeError, TypeError):
            deps = []
    if not deps:
        return True
    for dep_id in deps:
        dep = repo.get(conn, "unit", dep_id)
        if not dep or dep.get("status") != "done":
            return False
    return True


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _claim_sort_key(u: dict[str, Any]) -> tuple[int, float]:
    """Claim order: priority (high > medium > low), then rank ascending
    (None ranks last). Ties keep insertion order via stable sort."""
    priority = _PRIORITY_ORDER.get(str(u.get("priority") or "medium").lower(), 1)
    rank = u.get("rank")
    return (priority, float(rank) if isinstance(rank, (int, float)) else float("inf"))


def _find_member_unit(
    conn: sqlite3.Connection, member_id: str,
) -> dict[str, Any] | None:
    """Runnable unit for this member: claimed first, else atomically claim
    the highest-priority assigned, dependency-clear, pending unit.

    Without the assigned-unit fallback a completed dependency chain stalls
    forever — downstream units carry assignee_member_id but nothing ever
    claims them. The claim is a single guarded UPDATE (no race window).

    A pre-claimed unit (``claimed_by``) always wins over the scan — that is
    the Board's explicit unit-targeting lever; do not break it. The scan
    itself orders by priority, then rank, then insertion — before this it
    ran in bare insertion order and the Board's "adjust Unit priorities"
    charter power was connected to nothing (field failure 2026-07-13:
    UNT-BOARDBUILD set high to steer Wrench, Wrench claimed a medium unit
    with a lower rowid instead).
    """
    claimed = _find_claimed_unit(conn, member_id)
    if claimed and _deps_met(conn, claimed):
        return claimed

    candidates = sorted(
        repo.find(conn, "unit", assignee_member_id=member_id),
        key=_claim_sort_key,
    )
    for u in candidates:
        if u.get("status") != "pending" or u.get("claimed_by"):
            continue
        if not _deps_met(conn, u):
            continue
        row = conn.execute(
            "UPDATE unit SET claimed_by = ?, updated_at = datetime('now') "
            "WHERE id = ? AND claimed_by IS NULL RETURNING id",
            (member_id, u["id"]),
        ).fetchone()
        if row:
            conn.commit()
            return repo.get(conn, "unit", u["id"])
    return None


# ---------------------------------------------------------------------------
# Runner factory
# ---------------------------------------------------------------------------

def make_runner(
    firm_id: str,
    cwd: str,
) -> Any:
    """Create a runner callback matching ``RunMemberFn`` contract.

    Args:
        firm_id: Firm scope.
        cwd: Working directory for prompt assembly and spawned processes.

    Returns:
        Callable ``(conn, member_dict) -> result_dict``.
    """

    def _run_member(
        conn: sqlite3.Connection, member: dict[str, Any],
    ) -> dict[str, Any]:
        member_id = member["id"]
        now = datetime.now(tz=timezone.utc)

        # 1. Budget pre-flight
        budget_check = check_budget_preflight(conn, member_id)
        if not budget_check.allowed:
            return {"skipped": True, "reason": f"budget: {budget_check.reason}"}

        # 2. Find runnable unit (claimed, else claim next assigned dep-clear)
        unit = _find_member_unit(conn, member_id)
        if not unit:
            return {"skipped": True, "reason": "no units runnable (none claimed/assigned, or dependencies unmet)"}

        # 3. Resolve contract + runtime
        contract = _get_contract(conn, member)
        runtime = resolve_runtime(contract) if contract else ClaudeCodeRuntime()
        validation_config = _get_validation_config(contract)

        # 4. Create member_run
        run_id = next_id(conn, "member_run", firm_id)
        repo.create(conn, "member_run", {
            "id": run_id,
            "firm_id": firm_id,
            "member_id": member_id,
            "unit_id": unit["id"],
            "status": "running",
            "started_at": now.isoformat(),
            "invocation_source": "pulse",
        })

        # 5–13 must never leak a 'running' row: if any step raises, close the
        # run as failed before propagating (the orchestrator records the
        # exception, but nothing downstream would ever close the row — it
        # would sit as a zombie 'running' forever; ESC-D field report).
        try:
            return _execute_run(
                conn, firm_id, member, unit, contract, runtime,
                validation_config, run_id, cwd,
            )
        except Exception as exc:
            row = repo.get(conn, "member_run", run_id)
            if row and row.get("status") == "running":
                repo.update(conn, "member_run", run_id, {
                    "status": "failed",
                    "ended_at": datetime.now(tz=timezone.utc).isoformat(),
                    "error": json.dumps({
                        "type": "runner_exception",
                        "exception": type(exc).__name__,
                        "message": str(exc)[:2000],
                    }),
                })
            raise

    return _run_member


def _execute_run(
    conn: sqlite3.Connection,
    firm_id: str,
    member: dict[str, Any],
    unit: dict[str, Any],
    contract: dict[str, Any] | None,
    runtime: Any,
    validation_config: dict[str, Any] | None,
    run_id: str,
    cwd: str,
) -> dict[str, Any]:
    """Steps 5–13: invoke, parse, validate, finalize, persist completion."""
    member_id = member["id"]

    # 5. Invoke via Contract interface
    result = runtime.invoke(conn, contract or {}, member, unit, cwd=cwd, run_id=run_id)

    # 6. Store prompt snapshot
    repo.update(conn, "member_run", run_id, {
        "prompt_snapshot": result.prompt_snapshot,
    })

    # 7. Handle timeout / process error
    timeout = result.handle.metadata.get("timeout_sec", 300)

    if result.timed_out:
        # Partial stdout still carries whatever text the Member produced —
        # persist it; a timed-out deliverable is recoverable, a dropped one
        # is not. Bill it too: the tokens were burned whether or not the run
        # finished, and an unbilled failure makes every firm's spend figure
        # a floor that understates by exactly its failures (RUN-004,
        # 2026-07-13: ~20min of tokens, ledger showed zero).
        partial = parse_stream(result.stdout)
        _persist_final_text(conn, run_id, partial)
        update_budget_postrun(
            conn, member_id, firm_id, partial,
            run_id=run_id, unit_id=unit["id"],
        )
        repo.update(conn, "member_run", run_id, {
            "status": "timed_out",
            "ended_at": datetime.now(tz=timezone.utc).isoformat(),
            "error": json.dumps({"type": "timeout", "timeout_sec": timeout}),
        })
        return {"run_id": run_id, "status": "timed_out"}

    if result.returncode is not None and result.returncode != 0:
        partial = parse_stream(result.stdout)
        _persist_final_text(conn, run_id, partial)
        update_budget_postrun(
            conn, member_id, firm_id, partial,
            run_id=run_id, unit_id=unit["id"],
        )
        repo.update(conn, "member_run", run_id, {
            "status": "failed",
            "ended_at": datetime.now(tz=timezone.utc).isoformat(),
            "error": json.dumps({
                "type": "process_error",
                "returncode": result.returncode,
                "stderr": result.stderr[:2000],
            }),
        })
        return {"run_id": run_id, "status": "failed", "returncode": result.returncode}

    # 8. Parse
    parsed = parse_stream(result.stdout)

    # 8b. MCP startup guard — a run whose firm toolset never loaded must be
    # visibly degraded on its row, not silently "completed" without tools.
    mcp_missing = _mcp_startup_guard(conn, run_id, parsed, cwd)

    # 9. Validate
    validation_result = validate_output(parsed, validation_config, cwd, unit=unit)

    if not validation_result.passed and validation_config:
        max_retries = validation_config.get("max_retries", 0) if isinstance(validation_config, dict) else 0
        if max_retries > 0:
            # The failed attempt is a real run: bill it and close its row
            # honestly, then give the retry its OWN row linked via
            # retry_of_run_id. (Before this, the retry silently overwrote
            # the original attempt — its tokens never hit the budget and
            # retry_of_run_id pointed at itself.)
            update_budget_postrun(
                conn, member_id, firm_id, parsed,
                run_id=run_id, unit_id=unit["id"],
            )
            _persist_final_text(conn, run_id, parsed)
            repo.update(conn, "member_run", run_id, {
                "status": "failed",
                "ended_at": datetime.now(tz=timezone.utc).isoformat(),
                "validation_result": json.dumps({
                    "passed": False,
                    "details": validation_result.details,
                }),
                "error": json.dumps({"type": "validation_failed", "retried": True}),
            })
            original_run_id = run_id
            run_id = next_id(conn, "member_run", firm_id)
            repo.create(conn, "member_run", {
                "id": run_id,
                "firm_id": firm_id,
                "member_id": member_id,
                "unit_id": unit["id"],
                "status": "running",
                "started_at": datetime.now(tz=timezone.utc).isoformat(),
                "invocation_source": "pulse",
                "retry_of_run_id": original_run_id,
                "prompt_snapshot": result.prompt_snapshot,
            })

            failure_context = "\n".join(
                f"- {d['name']}: {d['message']}"
                for d in validation_result.details
                if not d.get("passed")
            )
            retry_model = result.handle.metadata.get("model")
            parsed = retry_on_failure(
                result.prompt_snapshot,
                failure_context,
                lambda p: spawn_member_run(
                    p, timeout_sec=timeout, cwd=cwd, model=retry_model,
                    member_id=member_id, firm_id=firm_id,
                ),
                parse_stream,
            )
            # The retry is a fresh spawn with its own init — guard its row too.
            mcp_missing = _mcp_startup_guard(conn, run_id, parsed, cwd)
            validation_result = validate_output(parsed, validation_config, cwd, unit=unit)

    # 10. Rate limit awareness
    rate_warning = check_rate_limit(
        parsed.get("rate_limit_events", []),
        alert_threshold_pct=_get_pulse_config_value(
            contract, "alert_threshold_pct", 80,
        ),
    )

    # 11. Budget post-run
    update_budget_postrun(
        conn, member_id, firm_id, parsed,
        run_id=run_id, unit_id=unit["id"],
    )

    # 12. Finalize member_run
    final_status = "completed" if validation_result.passed else "failed"
    _persist_final_text(conn, run_id, parsed)
    repo.update(conn, "member_run", run_id, {
        "status": final_status,
        "ended_at": datetime.now(tz=timezone.utc).isoformat(),
        "validation_result": json.dumps({
            "passed": validation_result.passed,
            "details": validation_result.details,
        }),
    })

    # 13. Completion persistence — a validated run MUST flip its Unit to
    # done (audit record + AC rollup via the service), or every future
    # pulse re-dispatches the same finished work and dependents never
    # unblock. Runner-owned per the relay seam-4 convention: the harness,
    # not the model, is the completion authority — hence system_context():
    # the authority gate must read this as the harness acting, even when the
    # pulse itself was fired from inside a Member run's process tree.
    if validation_result.passed:
        _register_deliverables(
            conn, firm_id, unit, member_id, parsed, validation_config, cwd,
        )
        with system_context():
            complete_unit(conn, firm_id, unit["id"], member_id, run_id=run_id)

    out: dict[str, Any] = {
        "run_id": run_id,
        "status": final_status,
        "text_length": len(parsed.get("text", "")),
        "usage": parsed.get("usage", {}),
        "cost": parsed.get("total_cost_usd"),
        "validation_passed": validation_result.passed,
        "rate_limit_warning": rate_warning,
    }
    if mcp_missing:
        out["mcp_degraded"] = mcp_missing
    return out
