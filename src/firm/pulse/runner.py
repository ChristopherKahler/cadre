"""PULSE runner — the orchestrator callback that chains all subsystems.

Implements the ``RunMemberFn`` contract: ``(conn, member_dict) -> result_dict``.
Chains: budget check → unit lookup → resolve runtime → member_run create →
Contract invoke → parse → validate → retry → budget update → finalize.
"""

from __future__ import annotations

import json
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
from firm.pulse.spawn import spawn_member_run
from firm.pulse.validate import retry_on_failure, validate_output
from firm.services._id import next_id
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


def _find_member_unit(
    conn: sqlite3.Connection, member_id: str,
) -> dict[str, Any] | None:
    """Runnable unit for this member: claimed first, else atomically claim
    the next assigned, dependency-clear, pending unit.

    Without the assigned-unit fallback a completed dependency chain stalls
    forever — downstream units carry assignee_member_id but nothing ever
    claims them. The claim is a single guarded UPDATE (no race window).
    """
    claimed = _find_claimed_unit(conn, member_id)
    if claimed and _deps_met(conn, claimed):
        return claimed

    for u in repo.find(conn, "unit", assignee_member_id=member_id):
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

        # 5. Invoke via Contract interface
        result = runtime.invoke(conn, contract or {}, member, unit, cwd=cwd)

        # 6. Store prompt snapshot
        repo.update(conn, "member_run", run_id, {
            "prompt_snapshot": result.prompt_snapshot,
        })

        # 7. Handle timeout / process error
        timeout = result.handle.metadata.get("timeout_sec", 300)

        if result.timed_out:
            repo.update(conn, "member_run", run_id, {
                "status": "timed_out",
                "ended_at": datetime.now(tz=timezone.utc).isoformat(),
                "error": json.dumps({"type": "timeout", "timeout_sec": timeout}),
            })
            return {"run_id": run_id, "status": "timed_out"}

        if result.returncode is not None and result.returncode != 0:
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

        # 9. Validate
        validation_result = validate_output(parsed, validation_config, cwd)
        retry_run_id = None

        if not validation_result.passed and validation_config:
            max_retries = validation_config.get("max_retries", 0) if isinstance(validation_config, dict) else 0
            if max_retries > 0:
                failure_context = "\n".join(
                    f"- {d['name']}: {d['message']}"
                    for d in validation_result.details
                    if not d.get("passed")
                )
                retry_parsed = retry_on_failure(
                    result.prompt_snapshot,
                    failure_context,
                    lambda p: spawn_member_run(p, timeout_sec=timeout, cwd=cwd),
                    parse_stream,
                )
                # Use retry result
                parsed = retry_parsed
                validation_result = validate_output(parsed, validation_config, cwd)
                retry_run_id = run_id  # Link retry to original

        # 10. Rate limit awareness
        rate_warning = check_rate_limit(
            parsed.get("rate_limit_events", []),
            alert_threshold_pct=_get_pulse_config_value(
                contract, "alert_threshold_pct", 80,
            ),
        )

        # 11. Budget post-run
        update_budget_postrun(conn, member_id, firm_id, parsed)

        # 12. Finalize member_run
        final_status = "completed" if validation_result.passed else "failed"
        update_data: dict[str, Any] = {
            "status": final_status,
            "ended_at": datetime.now(tz=timezone.utc).isoformat(),
            "validation_result": json.dumps({
                "passed": validation_result.passed,
                "details": validation_result.details,
            }),
        }
        if retry_run_id:
            update_data["retry_of_run_id"] = retry_run_id

        repo.update(conn, "member_run", run_id, update_data)

        # 13. Completion persistence — a validated run MUST flip its Unit to
        # done (audit record + AC rollup via the service), or every future
        # pulse re-dispatches the same finished work and dependents never
        # unblock. Runner-owned per the relay seam-4 convention: the harness,
        # not the model, is the completion authority.
        if validation_result.passed:
            complete_unit(conn, firm_id, unit["id"], member_id, run_id=run_id)

        return {
            "run_id": run_id,
            "status": final_status,
            "text_length": len(parsed.get("text", "")),
            "usage": parsed.get("usage", {}),
            "cost": parsed.get("total_cost_usd"),
            "validation_passed": validation_result.passed,
            "rate_limit_warning": rate_warning,
        }

    return _run_member
