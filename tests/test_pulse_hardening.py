"""Pulse hardening: durable PATH on dispatch + accurate cause/fix diagnosis
(fork cadre-pulse-hardening).

Guards the recurring field failure (ESC-008/009/010/015): systemd --user starts
with a bare PATH, the dispatched pulse can't resolve firm tools, every member
skips, and the escalation misdiagnoses a PATH problem as a dead credential
("usually a re-login").
"""

from __future__ import annotations

import os
from pathlib import Path

from firm.dashboard.server import _pulse_path
from firm.pulse.preflight import _fix_for


def test_pulse_path_is_full_even_from_a_bare_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/bin")   # the bare systemd --user PATH
    segs = _pulse_path(tmp_path).split(os.pathsep)
    # the dirs a bare PATH drops are restored — this is the whole fix
    assert str(Path.home() / ".local" / "bin") in segs
    assert str(tmp_path / ".firm" / "bin") in segs
    assert "/usr/bin" in segs        # system floor, even if the env had none
    assert "/bin" in segs            # the inherited entry is kept, not lost
    # no duplicates
    assert len(segs) == len(set(segs))


def test_fix_text_routes_credential_to_relogin():
    fix = _fix_for("installed but not signed in — the identity probe failed").lower()
    assert "re-authenticate" in fix or "re-login" in fix
    assert "environment" not in fix   # not a PATH/env fix


def test_fix_text_routes_path_miss_away_from_relogin():
    fix = _fix_for("`gws-acct` did not resolve on this process's PATH — "
                   "searched: /usr/bin").lower()
    # the ESC-015 bug: a PATH problem must NOT be told to re-login
    assert "re-login" not in fix and "re-authenticate" not in fix
    assert "path" in fix or "environment" in fix
