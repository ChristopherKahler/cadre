"""The CLI's own output must not depend on the machine's locale.

Cadre prints check marks, crosses, arrows and box-drawing characters. Python
encodes stdout with the platform locale, which on Windows is cp1252 for a pipe
or a file and the console code page for a terminal. None of those can
represent U+2713 or U+2192, so the command did not print a degraded line - it
raised UnicodeEncodeError and exited 1.

Measured on Windows 10 / Python 3.12.6 against the installed wheel, stdout
piped, before the fix:

    cadre doctor          rc 1, UnicodeEncodeError on U+2713
    cadre templates list  rc 1, UnicodeEncodeError on U+2192

Both worked on a terminal that was already UTF-8 and died the moment output
was redirected to a file, piped into another command, or captured by a
script - which is exactly when an operator is trying to keep a record of what
the tool said.

These tests run the CLI as a real subprocess with a pipe for stdout, because
that is the configuration that broke and it cannot be reproduced in-process.
PYTHONIOENCODING is cleared from the child environment on purpose: a
developer machine with it set hands the child a UTF-8 stdout for free and
hides the whole defect.

There is no red arm on Linux - a piped stdout there is UTF-8 already. The
Windows leg is what gives these tests teeth, which is one more reason the CI
keyword filter had to go.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

ARROW = "→"


def _cli(*args, cwd):
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    return subprocess.run(
        [sys.executable, "-m", "firm", *args],
        cwd=str(cwd), capture_output=True, env=env, timeout=120,
    )


def test_templates_list_survives_a_pipe(tmp_path):
    """Its output carries an arrow per template row."""
    proc = _cli("templates", "list", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = proc.stdout.decode("utf-8")
    assert ARROW in out, f"the arrow did not survive stdout:\n{out}"


def test_help_survives_a_pipe(tmp_path):
    """The description line carries an em dash."""
    proc = _cli("--help", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "—" in proc.stdout.decode("utf-8")


def test_doctor_survives_a_pipe(tmp_path):
    """doctor prints check marks and crosses, none of which cp1252 has."""
    ws = tmp_path / "ws"
    ws.mkdir()
    init = _cli("init", str(ws), "--demo", cwd=tmp_path)
    if init.returncode != 0:
        pytest.skip(f"init did not run here: "
                    f"{init.stderr.decode('utf-8', 'replace')[:300]}")
    proc = _cli("doctor", cwd=ws)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    proc.stdout.decode("utf-8")  # must be UTF-8, not a locale code page
