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
) -> dict[str, Any]:
    """Check that expected output files exist on disk.

    Looks for file paths mentioned in tool_calls (Write/Edit tools) and
    verifies they exist. If no tool_calls with file outputs, passes by default.
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
            "passed": True,
            "message": "No file outputs to verify",
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


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------

VALIDATOR_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "file_exists": _validate_file_exists,
    "min_word_count": _validate_min_word_count,
    "ac_self_report": _validate_ac_self_report,
}


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
        validation_config: From contract.validation_config JSON. None = skip.
        cwd: Working directory for file-based validators.

    Returns:
        ValidationResult with per-validator details.
    """
    if not validation_config:
        return ValidationResult(passed=True)

    if isinstance(validation_config, str):
        try:
            validation_config = json.loads(validation_config)
        except (json.JSONDecodeError, TypeError):
            return ValidationResult(passed=True)

    if not validation_config.get("enabled", True):
        return ValidationResult(passed=True)

    validator_names = validation_config.get("validators", [])
    if not isinstance(validator_names, list) or not validator_names:
        return ValidationResult(passed=True)

    details: list[dict[str, Any]] = []
    for name in validator_names:
        fn = VALIDATOR_REGISTRY.get(name)
        if fn is None:
            details.append({
                "name": name,
                "passed": False,
                "message": f"Unknown validator: {name}",
            })
            continue
        detail = fn(result, cwd)
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
