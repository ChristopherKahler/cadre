"""The acceptance harness must not die on its own report.

`scripts/acceptance-e2e.py` echoes child stdout, and `cadre doctor` prints
U+2713. On Windows a redirected or piped stdout is cp1252, which has no U+2713,
so printing it raises UnicodeEncodeError and the harness exits 1 — a failure a
reader cannot tell apart from the product breaking. An instrument that fails on
its own reporting is worse than no instrument.

Measured on a real Windows host, PYTHONIOENCODING cleared:

    sys.stdout.encoding, redirected   cp1252
    print('\\u2713'), redirected       rc 1, UnicodeEncodeError, EMPTY file
    same after reconfigure            rc 0, bytes E2 9C 93

Two arms, both in a real child process with a real pipe, because in-process
assertions cannot see the platform behaviour that causes this:

  RED    a child that does NOT reconfigure fails to emit U+2713 through a pipe
         on a host whose locale codec cannot encode it
  GREEN  a child that DOES reconfigure emits it, and the bytes round-trip

The red arm is skipped where the locale codec can encode U+2713 (any UTF-8
host, i.e. every Linux and macOS runner), because there the defect cannot be
reproduced and a "pass" there would mean nothing. It is not skipped by
PLATFORM — it is skipped by whether the fault is reachable, so the arm arms
itself on any host that can actually exhibit it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "scripts" / "acceptance-e2e.py"

CHECK = "\u2713"

RECONFIGURE = (
    "import sys\n"
    "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
    "sys.stdout.write('\\u2713')\n"
)
BARE = "import sys\nsys.stdout.write('\\u2713')\n"


def _locale_codec_can_encode_check() -> bool:
    """Can this host's default stdout codec carry U+2713 at all?

    Decides whether the RED arm is reachable. Uses a child with a pipe, since
    the parent's own stdout under pytest is already captured and reconfigured.
    """
    p = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.stdout.write(sys.stdout.encoding or '')"],
        capture_output=True, env=None, timeout=60,
    )
    enc = (p.stdout or b"").decode("ascii", errors="replace").strip().lower()
    if not enc:
        return True  # unknown: treat the fault as unreachable, skip the arm
    try:
        CHECK.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, timeout=60)


def test_the_harness_file_is_there_to_check() -> None:
    """A reader that reports zero must first prove it can see."""
    assert HARNESS.is_file(), f"acceptance harness not found at {HARNESS}"
    assert len(HARNESS.read_text(encoding="utf-8")) > 4000, (
        "the harness file is too small to be the real one; the assertions "
        "below would be checking almost nothing"
    )


def test_the_harness_makes_its_own_output_utf8_safe() -> None:
    """The fix is present, and main() does it BEFORE anything can print."""
    src = HARNESS.read_text(encoding="utf-8")
    assert "_make_output_utf8_safe" in src, (
        "the harness does not make its own output utf-8 safe; on Windows it "
        "will exit 1 with UnicodeEncodeError the moment it echoes a child that "
        "printed U+2713, and that reads as a product failure"
    )
    assert "def main() -> int:\n    _make_output_utf8_safe()" in src, (
        "the call is not the first statement in main(); anything that prints "
        "before it is still exposed"
    )
    assert 'errors="replace"' in src, (
        "strict errors would still take the harness down on one stray "
        "undecodable byte from some child"
    )
    assert "sys.stdout, sys.stderr" in src, "stderr is not covered"


def test_green_arm_a_reconfigured_child_emits_the_check_mark() -> None:
    """The fix works through a real pipe, on whatever host is running this."""
    p = _run(RECONFIGURE)
    assert p.returncode == 0, (
        f"rc={p.returncode} stderr={(p.stderr or b'').decode('utf-8', 'replace')[-300:]}"
    )
    assert p.stdout == CHECK.encode("utf-8"), (
        f"expected {CHECK.encode('utf-8')!r}, got {p.stdout!r}"
    )


@pytest.mark.skipif(
    _locale_codec_can_encode_check(),
    reason="this host's stdout codec can encode U+2713, so the defect is not "
           "reachable here and a passing red arm would prove nothing",
)
def test_red_arm_a_bare_child_cannot_emit_it_where_the_codec_lacks_it() -> None:
    """CONTROL: the defect really does exist on a host that can exhibit it.

    Without this, the green arm above is satisfied on every machine and the fix
    could be removed with nothing going red.
    """
    p = _run(BARE)
    assert p.returncode != 0, (
        "a child that did not reconfigure managed to write U+2713 through a "
        "pipe on a host whose codec cannot encode it — the premise of this "
        "whole guard is wrong, re-measure before trusting the green arm"
    )
    err = (p.stderr or b"").decode("utf-8", errors="replace")
    assert "UnicodeEncodeError" in err, (
        f"failed for some other reason than the encode: {err[-300:]}"
    )
