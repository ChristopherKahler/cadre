"""Output validation framework for PULSE Member runs.

Runs configured validators against Member run output and provides
Ralph Wiggum retry logic: on failure, spawn a fresh session with
failure context appended to the prompt.

Validators are simple, fast heuristics — no network calls, no AI.
"""

from __future__ import annotations

import dataclasses
import json
import re
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


_DELETION_TOKEN_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)(rm|unlink|rmdir|mv)\s|shutil\.rmtree|os\.(?:remove|unlink)"
)


def _declared_outputs(unit: dict[str, Any] | None) -> list[str]:
    """Paths the unit DECLARES as deliverables (``unit.outputs``).

    Entries may be bare strings or ``{"path": ...}`` dicts; anything else is
    skipped. Returns [] when the unit declares nothing.
    """
    if not unit:
        return []
    raw = unit.get("outputs")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            paths.append(entry.strip())
        elif isinstance(entry, dict) and entry.get("path"):
            paths.append(str(entry["path"]))
    return paths


def _artifact_failures(
    paths: list[str], cwd: str, *, min_bytes: int = 1,
) -> list[str]:
    """Assert the artifact, never the container (tapir's rule, 2026-07-13).

    A declared deliverable must exist and be non-trivial: a file needs
    ``>= min_bytes`` bytes; a directory needs at least one real file of
    ``>= min_bytes`` bytes somewhere inside it. An empty dir passing a
    ``[ -d ... ]`` check is exactly how UNT-TOOLING's export templates went
    green while 1.2GB sat unextracted. Returns human-readable failure lines,
    [] when every artifact holds up.
    """
    import os

    failures: list[str] = []
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(cwd, p)
        if not os.path.exists(full):
            failures.append(f"{p}: declared deliverable does not exist")
        elif os.path.isdir(full):
            has_content = any(
                os.path.getsize(os.path.join(root, f)) >= min_bytes
                for root, _dirs, files in os.walk(full)
                for f in files
            )
            if not has_content:
                failures.append(
                    f"{p}: declared deliverable is an empty directory"
                )
        elif os.path.getsize(full) < min_bytes:
            failures.append(
                f"{p}: declared deliverable is empty ({os.path.getsize(full)} bytes)"
            )
    return failures


def _validate_file_exists(
    result: dict[str, Any],
    cwd: str,
    *,
    require_written: bool = False,
    unit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check that expected output files exist on disk.

    Declared-deliverables mode: when the unit declares ``outputs``, THOSE
    are the deliverables — each must exist and be non-trivial (see
    ``_artifact_failures``), and incidental writes are ignored entirely. A
    member's scratch files are its own business; the unit's contract is the
    declared artifact list.

    Fallback (no declared outputs): looks for file paths mentioned in
    tool_calls (Write/Edit tools) and verifies they exist. If no tool_calls
    with file outputs, passes by default — UNLESS ``require_written`` is
    set, in which case a run that wrote no file fails. Set
    ``require_written`` on contracts whose units always produce a file
    deliverable (e.g. a Novelist's chapter): it is what stops a blocked or
    no-op run from completing a unit it never actually drafted.

    Cleanup is not failure: a written file that is missing at run end does
    NOT fail the run when the run's own Bash calls show deletion evidence
    for it (an ``rm``/``mv``/``unlink`` naming the path or its basename).
    A member that scratches two temp files and correctly deletes them did
    its job — before this, that run was marked failed and Ralph-Wiggum
    retried at full price (field failure 2026-07-13: crows-and-pawns
    RUN-007 burned $7.76 being punished for cleaning up after itself; the
    retry did identical work for $1.57). With ``require_written`` set, at
    least one written file must SURVIVE cleanup — deleting every file you
    wrote is still not a deliverable.
    """
    import os

    declared = _declared_outputs(unit)
    if declared:
        failures = _artifact_failures(declared, cwd)
        if failures:
            return {
                "name": "file_exists",
                "passed": False,
                "message": "Declared deliverables failed: " + "; ".join(failures),
            }
        return {
            "name": "file_exists",
            "passed": True,
            "message": (
                f"All {len(declared)} declared deliverable(s) exist and are "
                "non-empty"
            ),
        }

    tool_calls = result.get("tool_calls", [])
    written_files: list[str] = []
    deletion_commands: list[str] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name", "")
        inp = tc.get("input", {})
        if not isinstance(inp, dict):
            continue
        if name in ("Write", "Edit") and inp.get("file_path"):
            written_files.append(inp["file_path"])
        elif name == "Bash":
            cmd = inp.get("command") or ""
            if isinstance(cmd, str) and _DELETION_TOKEN_RE.search(cmd):
                deletion_commands.append(cmd)

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

    def _deleted_by_run(fp: str) -> bool:
        base = os.path.basename(fp)
        return any(fp in cmd or base in cmd for cmd in deletion_commands)

    surviving: list[str] = []
    cleaned: list[str] = []
    missing: list[str] = []
    for fp in written_files:
        if os.path.exists(fp):
            surviving.append(fp)
        elif _deleted_by_run(fp):
            cleaned.append(fp)
        else:
            missing.append(fp)

    if missing:
        return {
            "name": "file_exists",
            "passed": False,
            "message": f"Missing files: {', '.join(missing)}",
        }
    if require_written and not surviving:
        return {
            "name": "file_exists",
            "passed": False,
            "message": (
                f"All {len(written_files)} written file(s) were deleted by the "
                "run — expected a surviving file deliverable"
            ),
        }
    msg = f"All {len(surviving)} output files exist"
    if cleaned:
        msg += f" ({len(cleaned)} scratch file(s) cleaned up by the run)"
    return {"name": "file_exists", "passed": True, "message": msg}


def _validate_ac_script(
    result: dict[str, Any],
    cwd: str,
    *,
    unit: dict[str, Any] | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    """Execute the unit's machine-checkable acceptance criteria.

    An acceptance criterion that names a check script (a ``.sh``/``.bash``/
    ``.py`` path) is executable law, not prose: this validator extracts every
    script path from the unit's ``acceptance_criteria``, runs each from the
    firm workspace, and requires exit 0. A referenced script that does not
    exist fails — an AC pointing at a check nobody wrote is an unmet AC.

    This is what stops a member from passing by writing a report about
    itself (field failure 2026-07-13: UNT-TOOLING went green with export
    templates missing entirely, while its own AC said "when
    scripts/verify_toolchain.sh runs, then it exits 0" — nothing ever ran
    it). Prose-only ACs pass with an explicit "unverified" note: honesty
    over theater, and no false-fails for firms whose ACs carry no scripts.

    Convention for Boards: name a script in an AC only if running it IS the
    check. Scripts must resolve inside the firm workspace; paths escaping it
    are ignored.
    """
    import os
    import re
    import subprocess

    if not unit:
        return {
            "name": "ac_script",
            "passed": True,
            "message": "No unit context — nothing to check",
        }

    raw = unit.get("acceptance_criteria")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = [raw]
    if not raw:
        return {
            "name": "ac_script",
            "passed": True,
            "message": "Unit has no acceptance criteria",
        }
    if not isinstance(raw, list):
        raw = [raw]

    texts: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            texts.append(entry)
        elif isinstance(entry, dict):
            texts.append(str(entry.get("text") or entry.get("criterion") or ""))

    script_re = re.compile(r"[\w./-]+\.(?:sh|bash|py)\b")
    workspace = os.path.realpath(cwd)
    scripts: list[str] = []
    for text in texts:
        for candidate in script_re.findall(text):
            resolved = os.path.realpath(os.path.join(cwd, candidate))
            if not resolved.startswith(workspace + os.sep):
                continue  # escapes the firm workspace — not ours to run
            if resolved not in scripts:
                scripts.append(resolved)

    # Artifact floor — declared deliverables must hold up regardless of what
    # the check scripts claim. A vacuous script ([ -d ... ] on an empty dir)
    # must not be able to bless a unit whose artifacts aren't there.
    failures: list[str] = _artifact_failures(_declared_outputs(unit), cwd)

    if not scripts:
        n = len([t for t in texts if t.strip()])
        if failures:
            return {
                "name": "ac_script",
                "passed": False,
                "message": "AC check failed: " + "; ".join(failures),
            }
        return {
            "name": "ac_script",
            "passed": True,
            "message": f"No check scripts named in ACs ({n} prose criteria unverified)",
        }

    for script in scripts:
        rel = os.path.relpath(script, workspace)
        if not os.path.exists(script):
            failures.append(f"{rel}: referenced by an AC but does not exist")
            continue
        runner = ["bash", script] if not script.endswith(".py") else ["python3", script]
        try:
            proc = subprocess.run(
                runner, cwd=cwd, capture_output=True, text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{rel}: timed out after {timeout_sec}s")
            continue
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            failures.append(f"{rel}: exit {proc.returncode} — {tail}")

    if failures:
        return {
            "name": "ac_script",
            "passed": False,
            "message": "AC check failed: " + "; ".join(failures),
        }
    return {
        "name": "ac_script",
        "passed": True,
        "message": (
            f"All {len(scripts)} AC check script(s) passed"
            " and declared deliverables hold up"
        ),
    }


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
    "ac_script": _validate_ac_script,
    "sql_guard": _validate_sql_guard,
}

# Validators that receive the unit row (declared outputs, acceptance criteria).
_UNIT_AWARE = {"file_exists", "ac_script"}


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
    unit: dict[str, Any] | None = None,
) -> ValidationResult:
    """Run configured validators against a run result.

    Args:
        result: Parsed run result from parse_stream().
        validation_config: From contract.validation_config JSON. None = skip
            the configured validators (the always-on floor still applies).
        cwd: Working directory for file-based validators.
        unit: The unit row this run served, for unit-aware validators
            (declared outputs, acceptance criteria). None keeps every
            validator in its unit-less fallback behavior.

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
        if name in _UNIT_AWARE:
            params.setdefault("unit", unit)
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
