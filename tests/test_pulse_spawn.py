"""Tests for firm.pulse.spawn and firm.pulse.parser modules."""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest

from firm.pulse.parser import parse_stream
from firm.pulse.spawn import SpawnResult, _active_pids, _CLAUDE_FLAGS, spawn_member_run


# ═══════════════════════════════════════════════════════════════════════════
# Parser tests (pure function — no subprocess)
# ═══════════════════════════════════════════════════════════════════════════


def _make_event(etype: str, **kwargs) -> str:
    """Build a single NDJSON line."""
    return json.dumps({"type": etype, **kwargs})


def _make_init_event(session_id: str = "ses-001") -> str:
    return _make_event("system", subtype="init", session_id=session_id)


def _make_assistant_event(text: str) -> str:
    return _make_event(
        "assistant",
        message={"content": [{"type": "text", "text": text}]},
    )


def _make_tool_use_event(name: str, tool_id: str, tool_input: dict) -> str:
    return _make_event(
        "assistant",
        message={
            "content": [
                {"type": "tool_use", "name": name, "id": tool_id, "input": tool_input},
            ]
        },
    )


def _make_rate_limit_event(utilization: float = 0.5) -> str:
    return _make_event(
        "rate_limit_event",
        rate_limit_info={
            "utilization": utilization,
            "resetsAt": 1700000000,
            "rateLimitType": "five_hour",
            "isUsingOverage": False,
        },
    )


def _make_result_event(
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 200,
    cache_create: int = 100,
    total_cost: float = 0.05,
    stop_reason: str = "end_turn",
    is_error: bool = False,
    session_id: str | None = None,
) -> str:
    data: dict = {
        "type": "result",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
        },
        "total_cost_usd": total_cost,
        "stop_reason": stop_reason,
        "is_error": is_error,
    }
    if session_id:
        data["session_id"] = session_id
    return json.dumps(data)


class TestParseStreamBasic:
    """Core parsing behavior."""

    def test_empty_string_returns_zeroed(self):
        result = parse_stream("")
        assert result["session_id"] is None
        assert result["text"] == ""
        assert result["usage"]["input_tokens"] == 0
        assert result["usage"]["output_tokens"] == 0
        assert result["usage"]["cache_read"] == 0
        assert result["usage"]["cache_create"] == 0
        assert result["total_cost_usd"] is None
        assert result["is_error"] is False
        assert result["stop_reason"] is None
        assert result["tool_calls"] == []
        assert result["rate_limit_events"] == []

    def test_malformed_lines_skipped(self):
        stdout = "not json\n{bad json\n\n"
        result = parse_stream(stdout)
        assert result["text"] == ""
        assert result["session_id"] is None

    def test_full_stream(self):
        lines = [
            _make_init_event("ses-abc"),
            _make_assistant_event("Hello world"),
            _make_result_event(
                input_tokens=1500,
                output_tokens=800,
                total_cost=0.12,
                stop_reason="end_turn",
            ),
        ]
        stdout = "\n".join(lines)
        result = parse_stream(stdout)

        assert result["session_id"] == "ses-abc"
        assert result["text"] == "Hello world"
        assert result["usage"]["input_tokens"] == 1500
        assert result["usage"]["output_tokens"] == 800
        assert result["total_cost_usd"] == 0.12
        assert result["stop_reason"] == "end_turn"
        assert result["is_error"] is False


class TestParseStreamText:
    """Assistant text extraction."""

    def test_multiple_text_blocks_joined(self):
        lines = [
            _make_assistant_event("Part one."),
            _make_assistant_event("Part two."),
            _make_result_event(),
        ]
        result = parse_stream("\n".join(lines))
        assert result["text"] == "Part one.\n\nPart two."

    def test_empty_text_blocks_skipped(self):
        lines = [
            _make_assistant_event(""),
            _make_assistant_event("Real content"),
            _make_result_event(),
        ]
        result = parse_stream("\n".join(lines))
        assert result["text"] == "Real content"


class TestParseStreamToolCalls:
    """Tool use extraction."""

    def test_tool_calls_extracted(self):
        lines = [
            _make_tool_use_event("Read", "tc-001", {"file_path": "/tmp/x"}),
            _make_tool_use_event("Write", "tc-002", {"file_path": "/tmp/y", "content": "z"}),
            _make_result_event(),
        ]
        result = parse_stream("\n".join(lines))
        assert len(result["tool_calls"]) == 2
        assert result["tool_calls"][0]["name"] == "Read"
        assert result["tool_calls"][0]["id"] == "tc-001"
        assert result["tool_calls"][1]["name"] == "Write"


class TestParseStreamRateLimits:
    """Rate limit event capture."""

    def test_rate_limit_events_captured(self):
        lines = [
            _make_init_event(),
            _make_rate_limit_event(0.75),
            _make_assistant_event("output"),
            _make_rate_limit_event(0.92),
            _make_result_event(),
        ]
        result = parse_stream("\n".join(lines))
        assert len(result["rate_limit_events"]) == 2
        assert result["rate_limit_events"][0]["utilization"] == 0.75
        assert result["rate_limit_events"][1]["utilization"] == 0.92
        assert result["rate_limit_events"][0]["rate_limit_type"] == "five_hour"


class TestParseStreamErrors:
    """Error detection."""

    def test_is_error_from_result(self):
        lines = [
            _make_result_event(is_error=True, stop_reason="error"),
        ]
        result = parse_stream("\n".join(lines))
        assert result["is_error"] is True

    def test_error_subtype(self):
        line = json.dumps({"type": "result", "subtype": "error", "usage": {}})
        result = parse_stream(line)
        assert result["is_error"] is True


class TestParseStreamCamelCase:
    """Handle camelCase variants from stream-json."""

    def test_camel_case_usage_fields(self):
        line = json.dumps({
            "type": "result",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cacheReadInputTokens": 30,
                "cacheCreationInputTokens": 20,
            },
            "total_cost_usd": 0.01,
            "stop_reason": "end_turn",
        })
        result = parse_stream(line)
        assert result["usage"]["cache_read"] == 30
        assert result["usage"]["cache_create"] == 20

    def test_camel_case_rate_limit(self):
        line = json.dumps({
            "type": "rate_limit_event",
            "rate_limit_info": {
                "utilization": 0.6,
                "resets_at": 1700000000,
                "rate_limit_type": "five_hour",
                "is_using_overage": True,
            },
        })
        result = parse_stream(line)
        assert result["rate_limit_events"][0]["resets_at"] == 1700000000
        assert result["rate_limit_events"][0]["is_using_overage"] is True


class TestParseStreamSessionId:
    """Session ID extraction from init and result events."""

    def test_session_id_from_init(self):
        lines = [_make_init_event("ses-init"), _make_result_event()]
        result = parse_stream("\n".join(lines))
        assert result["session_id"] == "ses-init"

    def test_session_id_fallback_to_result(self):
        lines = [_make_result_event(session_id="ses-result")]
        result = parse_stream("\n".join(lines))
        assert result["session_id"] == "ses-result"

    def test_init_takes_precedence_over_result(self):
        lines = [
            _make_init_event("ses-init"),
            _make_result_event(session_id="ses-result"),
        ]
        result = parse_stream("\n".join(lines))
        assert result["session_id"] == "ses-init"


# ═══════════════════════════════════════════════════════════════════════════
# Spawn tests (mocked subprocess)
# ═══════════════════════════════════════════════════════════════════════════


class TestSpawnCommand:
    """Verify correct command construction."""

    def test_builds_correct_args(self):
        mock_proc = mock.MagicMock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("stdout", "stderr")
        mock_proc.returncode = 0

        with (
            mock.patch(
                "firm.pulse.spawn.resolve_claude_bin",
                return_value=("/usr/bin/claude-test", "test"),
            ),
            mock.patch("firm.pulse.spawn.subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = spawn_member_run("test prompt", timeout_sec=60, cwd="/tmp")

        expected_cmd = ["/usr/bin/claude-test", *_CLAUDE_FLAGS, "-p", "test prompt"]
        mock_popen.assert_called_once_with(
            expected_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd="/tmp",
            env=mock.ANY,
        )
        assert result.returncode == 0
        assert result.stdout == "stdout"
        assert result.stderr == "stderr"
        assert result.pid == 12345
        assert result.timed_out is False

    def test_member_identity_exported_into_child_env(self):
        mock_proc = mock.MagicMock()
        mock_proc.pid = 7
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0

        with (
            mock.patch(
                "firm.pulse.spawn.resolve_claude_bin",
                return_value=("/usr/bin/claude-test", "test"),
            ),
            mock.patch(
                "firm.pulse.spawn.subprocess.Popen", return_value=mock_proc,
            ) as mock_popen,
        ):
            spawn_member_run("p", member_id="MEM-007", firm_id="lab")

        env = mock_popen.call_args.kwargs["env"]
        assert env["CADRE_MEMBER_ID"] == "MEM-007"
        assert env["FIRM_ID"] == "lab"

    def test_default_cwd_is_none(self):
        mock_proc = mock.MagicMock()
        mock_proc.pid = 1
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0

        with mock.patch("firm.pulse.spawn.subprocess.Popen", return_value=mock_proc) as mock_popen:
            spawn_member_run("prompt")

        assert mock_popen.call_args.kwargs["cwd"] is None


class TestSpawnTimeout:
    """Timeout handling."""

    def test_timeout_returns_timed_out_result(self):
        mock_proc = mock.MagicMock()
        mock_proc.pid = 99
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=60),
            ("partial", "err"),  # After kill + second communicate
        ]
        mock_proc.returncode = None

        with mock.patch("firm.pulse.spawn.subprocess.Popen", return_value=mock_proc):
            result = spawn_member_run("prompt", timeout_sec=60)

        assert result.timed_out is True
        assert result.returncode is None
        assert result.pid == 99
        mock_proc.kill.assert_called_once()


class TestSpawnProcessErrors:
    """Process launch failures."""

    def test_file_not_found(self):
        with mock.patch(
            "firm.pulse.spawn.subprocess.Popen",
            side_effect=FileNotFoundError("claude not found"),
        ):
            result = spawn_member_run("prompt")

        assert result.returncode == -1
        assert "claude not found" in result.stderr
        assert result.pid is None
        assert result.timed_out is False

    def test_os_error(self):
        with mock.patch(
            "firm.pulse.spawn.subprocess.Popen",
            side_effect=OSError("permission denied"),
        ):
            result = spawn_member_run("prompt")

        assert result.returncode == -1
        assert "permission denied" in result.stderr


class TestSpawnPidTracking:
    """PID lifecycle in _active_pids."""

    def test_pid_tracked_during_run(self):
        captured_pids: list[dict] = []

        mock_proc = mock.MagicMock()
        mock_proc.pid = 42

        def capture_communicate(timeout=None):
            # Snapshot _active_pids during the run
            captured_pids.append(dict(_active_pids))
            return ("out", "err")

        mock_proc.communicate.side_effect = capture_communicate
        mock_proc.returncode = 0

        with mock.patch("firm.pulse.spawn.subprocess.Popen", return_value=mock_proc):
            spawn_member_run("prompt")

        # During communicate, PID 42 should have been tracked
        assert 42 in captured_pids[0]
        # After completion, cleaned up
        assert 42 not in _active_pids

    def test_pid_cleaned_after_timeout(self):
        mock_proc = mock.MagicMock()
        mock_proc.pid = 77
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=60),
            ("", ""),
        ]

        with mock.patch("firm.pulse.spawn.subprocess.Popen", return_value=mock_proc):
            spawn_member_run("prompt", timeout_sec=60)

        assert 77 not in _active_pids


class TestSpawnModelOverride:
    """pulse_config.model → --model flag (per-contract cost lever)."""

    @staticmethod
    def _mock_proc():
        p = mock.MagicMock()
        p.pid = 7
        p.communicate.return_value = ("", "")
        p.returncode = 0
        return p

    def test_model_flag_included_when_set(self):
        with (
            mock.patch(
                "firm.pulse.spawn.resolve_claude_bin",
                return_value=("/usr/bin/claude-test", "test"),
            ),
            mock.patch(
                "firm.pulse.spawn.subprocess.Popen", return_value=self._mock_proc(),
            ) as mock_popen,
        ):
            spawn_member_run("p", model="haiku")
        cmd = mock_popen.call_args[0][0]
        i = cmd.index("--model")
        assert cmd[i + 1] == "haiku"
        assert cmd[-2:] == ["-p", "p"]

    def test_no_model_flag_by_default(self):
        with (
            mock.patch(
                "firm.pulse.spawn.resolve_claude_bin",
                return_value=("/usr/bin/claude-test", "test"),
            ),
            mock.patch(
                "firm.pulse.spawn.subprocess.Popen", return_value=self._mock_proc(),
            ) as mock_popen,
        ):
            spawn_member_run("p")
        assert "--model" not in mock_popen.call_args[0][0]


def test_contract_model_extraction():
    import json as _json
    from firm.contracts.claude_code import ClaudeCodeRuntime
    assert ClaudeCodeRuntime._get_model(
        {"pulse_config": _json.dumps({"timeout_sec": 600, "model": "haiku"})}
    ) == "haiku"
    assert ClaudeCodeRuntime._get_model({"pulse_config": _json.dumps({})}) is None
    assert ClaudeCodeRuntime._get_model({}) is None
    assert ClaudeCodeRuntime._get_model(
        {"pulse_config": {"model": "sonnet"}}
    ) == "sonnet"
