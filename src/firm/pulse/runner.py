"""PULSE runner — the orchestrator callback that chains all subsystems.

Implements the ``RunMemberFn`` contract: ``(conn, member_dict) -> result_dict``.
Chains: budget check → unit lookup → member_run create → prompt assembly →
spawn → parse → validate → retry → budget update → finalize.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from firm.core import repo
from firm.pulse.budget import (
    check_budget_preflight,
    check_rate_limit,
    update_budget_postrun,
)
from firm.pulse.parser import parse_stream
from firm.pulse.prompt import assemble_prompt
from firm.pulse.spawn import spawn_member_run
from firm.pulse.validate import retry_on_failure, validate_output
from firm.services._id import next_id


# ---------------------------------------------------------------------------
# Config extraction helpers
# ---------------------------------------------------------------------------

def _get_contract_config(
    conn: sqlite3.Connection, member: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Extract pulse_config, validation_config, budget_config from member's contract.

    Returns (pulse_config, validation_config, budget_config).
    pulse_config always returns a dict (with defaults if missing).
    """
    defaults: dict[str, Any] = {"timeout_sec": 300}
    contract_id = member.get("contract_id")
    if not contract_id:
        return defaults, None, None

    contract = repo.get(conn, "contract", contract_id)
    if not contract:
        return defaults, None, None

    pc = contract.get("pulse_config")
    if isinstance(pc, str):
        try:
            pc = json.loads(pc)
        except (json.JSONDecodeError, TypeError):
            pc = None
    pulse_config = pc if isinstance(pc, dict) else {}
    pulse_config.setdefault("timeout_sec", 300)

    vc = contract.get("validation_config")
    if isinstance(vc, str):
        try:
            vc = json.loads(vc)
        except (json.JSONDecodeError, TypeError):
            vc = None

    bc = contract.get("budget_config")
    if isinstance(bc, str):
        try:
            bc = json.loads(bc)
        except (json.JSONDecodeError, TypeError):
            bc = None

    return pulse_config, vc, bc


def _find_claimed_unit(
    conn: sqlite3.Connection, member_id: str,
) -> dict[str, Any] | None:
    """Find first pending/in_progress unit claimed by member."""
    units = repo.find(conn, "unit", claimed_by=member_id)
    for u in units:
        if u.get("status") in ("pending", "in_progress"):
            return u
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

        # 2. Find claimed unit
        unit = _find_claimed_unit(conn, member_id)
        if not unit:
            return {"skipped": True, "reason": "no units"}

        # 3. Extract config
        pulse_config, validation_config, budget_config = _get_contract_config(conn, member)

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

        # 5. Assemble prompt
        prompt = assemble_prompt(conn, firm_id, member_id, unit["id"], cwd=cwd)

        # 6. Store prompt snapshot
        repo.update(conn, "member_run", run_id, {
            "prompt_snapshot": prompt,
        })

        # 7. Spawn
        timeout = pulse_config.get("timeout_sec", 300)
        spawn_result = spawn_member_run(prompt, timeout_sec=timeout, cwd=cwd)

        # 8. Handle timeout / process error
        if spawn_result.timed_out:
            repo.update(conn, "member_run", run_id, {
                "status": "timed_out",
                "ended_at": datetime.now(tz=timezone.utc).isoformat(),
                "error": json.dumps({"type": "timeout", "timeout_sec": timeout}),
            })
            return {"run_id": run_id, "status": "timed_out"}

        if spawn_result.returncode is not None and spawn_result.returncode != 0:
            repo.update(conn, "member_run", run_id, {
                "status": "failed",
                "ended_at": datetime.now(tz=timezone.utc).isoformat(),
                "error": json.dumps({
                    "type": "process_error",
                    "returncode": spawn_result.returncode,
                    "stderr": spawn_result.stderr[:2000],
                }),
            })
            return {"run_id": run_id, "status": "failed", "returncode": spawn_result.returncode}

        # 9. Parse
        parsed = parse_stream(spawn_result.stdout)

        # 10. Validate
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
                    prompt,
                    failure_context,
                    lambda p: spawn_member_run(p, timeout_sec=timeout, cwd=cwd),
                    parse_stream,
                )
                # Use retry result
                parsed = retry_parsed
                validation_result = validate_output(parsed, validation_config, cwd)
                retry_run_id = run_id  # Link retry to original

        # 11. Rate limit awareness
        rate_warning = check_rate_limit(
            parsed.get("rate_limit_events", []),
            alert_threshold_pct=pulse_config.get("alert_threshold_pct", 80),
        )

        # 12. Budget post-run
        update_budget_postrun(conn, member_id, firm_id, parsed)

        # 13. Finalize member_run
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
