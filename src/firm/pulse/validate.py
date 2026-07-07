"""Output validation framework for PULSE Member runs.

Runs configured validators against Member run output and provides
Ralph Wiggum retry logic: on failure, spawn a fresh session with
failure context appended to the prompt.

Validators are simple, fast heuristics — no network calls, no AI.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ValidationResult:
    """Outcome of running validators against a run result."""

    passed: bool
    details: list[dict[str, Any]] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------

def _validate_min_word_count(
    result: dict[str, Any],
    cwd: str,
    *,
    threshold: int = 100,
) -> dict[str, Any]:
    """Check that assistant text output meets minimum word count."""
    text = result.get("text", "")
    count = len(text.split())
    passed = count >= threshold
    return {
        "name": "min_word_count",
        "passed": passed,
        "message": f"Word count: {count} (threshold: {threshold})",
    }


def _validate_ac_self_report(
    result: dict[str, Any],
    cwd: str,
) -> dict[str, Any]:
    """Check that output contains acceptance criteria self-report markers."""
    text = result.get("text", "")
    upper = text.upper()
    has_marker = (
        "ACCEPTANCE CRITERIA" in upper
        or "AC-" in text
        or "AC_" in text
    )
    return {
        "name": "ac_self_report",
        "passed": has_marker,
        "message": "AC self-report marker found" if has_marker else "No AC marker in output",
    }


def _validate_file_exists(
    result: dict[str, Any],
    cwd: str,
    *,
    require_written: bool = False,
) -> dict[str, Any]:
    """Check that expected output files exist on disk.

    Looks for file paths mentioned in tool_calls (Write/Edit tools) and
    verifies they exist. If no tool_calls with file outputs, passes by
    default — UNLESS ``require_written`` is set, in which case a run that
    wrote no file fails. Set ``require_written`` on contracts whose units
    always produce a file deliverable (e.g. a Novelist's chapter): it is
    what stops a blocked or no-op run from completing a unit it never
    actually drafted.
    """
    import os

    tool_calls = result.get("tool_calls", [])
    written_files: list[str] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name", "")
        inp = tc.get("input", {})
        if not isinstance(inp, dict):
            continue
        if name in ("Write", "Edit") and inp.get("file_path"):
            written_files.append(inp["file_path"])

    if not written_files:
        return {
            "name": "file_exists",
            "passed": not require_written,
            "message": (
                "Expected a written file deliverable, but the run wrote none"
                if require_written
                else "No file outputs to verify"
            ),
        }

    missing: list[str] = []
    for fp in written_files:
        if not os.path.exists(fp):
            missing.append(fp)

    passed = len(missing) == 0
    if passed:
        msg = f"All {len(written_files)} output files exist"
    else:
        msg = f"Missing files: {', '.join(missing)}"

    return {"name": "file_exists", "passed": passed, "message": msg}


def _validate_sql_guard(
    result: dict[str, Any],
    cwd: str,
    *,
    query: str,
    expect: str = "nonempty",
    message: str | None = None,
) -> dict[str, Any]:
    """Generic DB-state guard: run *query* against the firm DB and pass iff
    the result matches *expect* (``"nonempty"`` → ≥1 row, ``"empty"`` → 0
    rows). Connects through :func:`firm.core.db.db_connection`, so it honours
    ``CADRE_DB_URL`` — hitting Turso for a multiplayer firm and the local
    ``.firm/firm.db`` otherwise.

    The invariant lives in the contract, not here — this validator stays
    game-agnostic. Example (The Table): a DM whose turn must always hand the
    Board a move configures it to require a ``your_move`` newer than the last
    board move; a run that narrates but never closes the turn then fails and
    is Ralph-Wiggum-retried into closing it (field failure 2026-07-07: Dorn
    logged narration, skipped the your_move, and the move box never appeared).
    """
    from pathlib import Path

    from firm.core import db as _db

    try:
        with _db.db_connection(Path(cwd)) as conn:
            row = conn.execute(query).fetchone()
    except Exception as exc:  # a broken guard fails loud — never passes silently
        return {
            "name": "sql_guard",
            "passed": False,
            "message": f"sql_guard query error: {exc}",
        }

    has_row = row is not None
    passed = has_row if expect == "nonempty" else not has_row
    if passed:
        msg = f"sql_guard ok (expect={expect})"
    else:
        msg = message or (
            f"sql_guard failed (expect={expect}, "
            f"got {'a row' if has_row else 'no rows'})"
        )
    return {"name": "sql_guard", "passed": passed, "message": msg}


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------

VALIDATOR_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "file_exists": _validate_file_exists,
    "min_word_count": _validate_min_word_count,
    "ac_self_report": _validate_ac_self_report,
    "sql_guard": _validate_sql_guard,
}


# ---------------------------------------------------------------------------
# Always-on completion floor
# ---------------------------------------------------------------------------

def _nonempty_floor(result: dict[str, Any]) -> dict[str, Any] | None:
    """Universal guard: a run that produced NOTHING durable — no assistant
    text and no tool actions — must never complete its unit, even when the
    contract forgot to configure validation. Returns a failing detail on a
    true no-op, else None (nothing to add).

    This is the safety net for the mis-seed class where a firm ships with
    ``validation_config: None`` on every contract (wastelander, 2026-07):
    without it, seam-4 completion becomes vacuous and blocked/no-op runs
    flip units to done. It is deliberately narrow (only pure no-ops) so it
    can run unconditionally without false-failing legitimate work.
    """
    text = (result.get("text") or "").strip()
    tool_calls = result.get("tool_calls") or []
    if not text and not tool_calls:
        return {
            "name": "nonempty_floor",
            "passed": False,
            "message": "Run produced no assistant output and no tool actions — refusing to complete the unit",
        }
    return None


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate_output(
    result: dict[str, Any],
    validation_config: dict[str, Any] | None,
    cwd: str,
) -> ValidationResult:
    """Run configured validators against a run result.

    Args:
        result: Parsed run result from parse_stream().
        validation_config: From contract.validation_config JSON. None = skip
            the configured validators (the always-on floor still applies).
        cwd: Working directory for file-based validators.

    Returns:
        ValidationResult with per-validator details.

    Validator entries may be a bare name (``"file_exists"``) or a dict
    carrying params (``{"name": "file_exists", "require_written": true}``);
    extra keys are passed through as keyword args to the validator.
    """
    # Always-on floor runs before any early return — a no-op never completes.
    floor = _nonempty_floor(result)
    if floor is not None:
        return ValidationResult(passed=False, details=[floor])

    if not validation_config:
        return ValidationResult(passed=True)

    if isinstance(validation_config, str):
        try:
            validation_config = json.loads(validation_config)
        except (json.JSONDecodeError, TypeError):
            return ValidationResult(passed=True)

    if not isinstance(validation_config, dict):
        return ValidationResult(passed=True)

    if not validation_config.get("enabled", True):
        return ValidationResult(passed=True)

    validator_entries = validation_config.get("validators", [])
    if not isinstance(validator_entries, list) or not validator_entries:
        return ValidationResult(passed=True)

    details: list[dict[str, Any]] = []
    for entry in validator_entries:
        if isinstance(entry, dict):
            name = entry.get("name") or ""
            params = {k: v for k, v in entry.items() if k != "name"}
        else:
            name = entry
            params = {}
        fn = VALIDATOR_REGISTRY.get(name)
        if fn is None:
            details.append({
                "name": name,
                "passed": False,
                "message": f"Unknown validator: {name}",
            })
            continue
        detail = fn(result, cwd, **params)
        details.append(detail)

    all_passed = all(d.get("passed", False) for d in details)
    return ValidationResult(passed=all_passed, details=details)


# ---------------------------------------------------------------------------
# Ralph Wiggum retry
# ---------------------------------------------------------------------------

def retry_on_failure(
    original_prompt: str,
    failure_context: str,
    spawn_fn: Callable[[str], Any],
    parse_fn: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Spawn a fresh session with failure context appended.

    Args:
        original_prompt: The prompt from the failed run.
        failure_context: Description of what failed and why.
        spawn_fn: Callable that takes a prompt string and returns a SpawnResult-like object with .stdout.
        parse_fn: Callable that takes stdout string and returns parsed result dict.

    Returns:
        Parsed result dict from the retry run.
    """
    retry_prompt = (
        f"{original_prompt}\n\n"
        "---\n"
        "PREVIOUS ATTEMPT FAILED:\n"
        f"{failure_context}\n"
        "Retry with corrections. Address the issues above."
    )
    spawn_result = spawn_fn(retry_prompt)
    return parse_fn(spawn_result.stdout)
