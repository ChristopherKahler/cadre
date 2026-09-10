"""The CLI's own help must not name one scheduler backend as if it were the only one.

``firm.sched`` ships three backends and ``resolve_scheduler()`` picks by
``sys.platform``: systemd user timers on Linux/WSL2, launchd LaunchAgents on
macOS, Task Scheduler on Windows. Until 2026-09-10 the ``heartbeat``
subcommand's help said it manages "the per-firm systemd user timer" -- false
on two of the three platforms CI runs the suite on, and argparse help is the
first thing a new operator reads.

Nothing in the suite pinned it, so it stayed wrong through every green run on
all three OSes. The suite exercised the backends thoroughly (test_sched.py,
test_cli_heartbeat.py) and never once read what the CLI told the operator
those backends were.

The rule is not "never say systemd". It is: **if the CLI help names any one
backend's mechanism, it must name all three**, because naming exactly one is
what makes the sentence false on the other two.
"""

from __future__ import annotations

from firm.__main__ import _build_parser

_BT = chr(96)   # backtick, so this file stays free of shell-hostile characters

# The OS-specific mechanism words behind the three backends in firm.sched.
# Naming any word from one family obliges the text to name all three.
_FAMILIES = {
    "linux": ("systemd",),
    "macos": ("launchd", "launchagent"),
    "windows": ("task scheduler", "schtasks", "winsched"),
}

# The help string that shipped until 2026-09-10 and its replacement, with
# the source's em dash written as an ASCII hyphen so this file stays ASCII.
# Both are pinned here so the checker below is proved to discriminate rather
# than merely to return the empty set for everything.
_OLD = (
    "Autonomous pulse cadence - manage the per-firm systemd user "
    "timer that fires " + _BT + "cadre pulse" + _BT + " on an interval."
)
_NEW = (
    "Autonomous pulse cadence - manage the per-firm timer that fires "
    + _BT + "cadre pulse" + _BT + " on an interval, through the host "
    "scheduler: systemd user timers on Linux/WSL2, launchd on macOS, "
    "Task Scheduler on Windows."
)


def _families_named(text: str) -> set[str]:
    """Which scheduler families *text* names. Whitespace is normalised first
    because argparse wraps help across lines at the terminal width, so a
    two-word mechanism like "Task Scheduler" can arrive split."""
    low = " ".join(text.lower().split())
    return {
        family
        for family, words in _FAMILIES.items()
        if any(word in low for word in words)
    }


def _cli_help(monkeypatch) -> str:
    """The top-level help, which is where every subcommand's one-line help is
    rendered. COLUMNS is pinned so the wrap point cannot vary by host."""
    monkeypatch.setenv("COLUMNS", "200")
    return " ".join(_build_parser().format_help().split())


def test_cli_help_names_every_scheduler_backend_or_none(monkeypatch):
    named = _families_named(_cli_help(monkeypatch))
    assert named in (set(), {"linux", "macos", "windows"}), (
        "CLI help names scheduler families "
        + repr(sorted(named))
        + " and not the rest. firm.sched has three backends; naming one as "
        "if it were the mechanism is false on the other two platforms, both "
        "of which CI runs this suite on."
    )


def test_the_help_under_test_is_really_the_cli_help(monkeypatch):
    """Control. The all-or-none rule above is satisfied vacuously by an empty
    string, so assert the text really is the parser's help and really does
    carry the heartbeat entry the rule exists for."""
    text = _cli_help(monkeypatch)
    assert "heartbeat" in text
    assert "Autonomous pulse cadence" in text


def test_the_family_check_discriminates():
    """Red arm. The string that shipped must be rejected and its replacement
    accepted. A checker that always returned the empty set would pass the
    first test on any text at all; this is what makes that green mean
    something."""
    assert _families_named(_OLD) == {"linux"}
    assert _families_named(_NEW) == {"linux", "macos", "windows"}
