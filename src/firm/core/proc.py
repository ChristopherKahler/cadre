"""Reading a child process's output, decoded the same way on every host.

Python's text mode picks its decoder from the MACHINE's locale, never from
what the child actually writes. On Windows that is the ANSI code page --
cp1252 on a US install -- and under ``LC_ALL=C`` on POSIX it is ASCII. Every
stream Cadre reads is UTF-8: claude's JSON event stream, base's banner, git's
porcelain, the scheduler CLIs. ``text=True`` on its own therefore asks the
wrong codec, and it fails in two different ways, neither of which looks like a
decoding problem from the call site:

  STREAMED -- ``Popen(..., text=True)`` then ``for line in proc.stdout``
      the iteration raises ``UnicodeDecodeError`` part-way through the run.
      Founding a firm on Windows died here EVERY TIME (#114, measured
      2026-09-13 at 496e6283): job ``1349f14f6c35`` failed with "charmap codec
      can not decode byte 0x90 in position 394" and a null proposal. 0x90 is
      undefined in cp1252 and is the third byte of every box-drawing character
      in the U+25xx range; an em dash or a curly quote lands the same way, and
      a Claude JSON stream carries one almost every run. The job's broad
      ``except Exception`` turned that into a failed founding whose reported
      cause named a codec, so the operator saw a founding that could not be
      made to work.

  ONE-SHOT -- ``run(..., capture_output=True, text=True)``
      on Windows the decode happens inside ``Popen._readerthread``, so the
      exception kills the reader thread and ``run`` RETURNS -- rc 0, stdout
      None. Callers catching only ``OSError``/``TimeoutExpired`` see no error
      at all and read the result as "the child said nothing". Measured on the
      same day: ``base_domain.rule_count`` answering None, so a firm's base
      domain seed skipped in silence and the firm was reported founded with a
      wire that reached nobody. It is intermittent because base's update
      banner -- the thing carrying the box characters -- is time-gated.

THE POLICY IS UTF-8 WITH ``errors="replace"``. Replacement rather than strict
on purpose: one stray undecodable byte from some child must cost a character,
never a founding run or a pulse. ``scripts/acceptance-e2e.py`` reached the
same conclusion for the same reason and decodes its children's bytes by hand;
``tests/hooks/test_session_pulse_e2e.py`` says it in as many words for the
hook. This module is where the product itself keeps that decision.

BOTH WRAPPERS SPELL ``encoding="utf-8"`` AND ``errors="replace"`` OUT AS
LITERALS in the subprocess call, rather than naming module constants or
forwarding a dict. That is deliberate and it is not style: the sweep in
``tests/test_child_output_is_decoded_as_utf8.py`` reads call sites with
``ast``, and a keyword whose value is a Name is a value that file cannot
resolve. Written this way the policy module passes its own guard by the same
rule as every other call site and needs no exemption, and an exemption is
where a guard starts to rot. The pair of them is deliberately boring to read
and impossible to satisfy accidentally.

``NoOutput`` is the second half of #114. Where a child's OUTPUT is the only
signal it has -- ``base rule list`` exits 0 for a populated domain, an empty
one and a domain that never existed, so its exit code discriminates nothing --
silence must be a failure carrying a reason, never data. Opt in per call with
``require_output=True``; it is off by default because most callers already
have a returncode worth trusting.

NO WINDOW (issue #119). On Windows, a console program started by a process
that has no console of its own gets a new console, and a new console has a
window: every scheduled pulse drew one on the operator's desktop, and closing
it killed the pulse. So both wrappers ask, at every spawn, whether this process
is attached to a console. Attached, the child inherits that console and opens
nothing, and Ctrl+C typed in a terminal still reaches it. Not attached, or no
answer, the child is started with ``CREATE_NO_WINDOW``. The question is put to
``GetConsoleProcessList``; ``GetConsoleWindow`` was measured returning 0 in
attached processes too, so it cannot tell the two apart. ``CREATE_NEW_CONSOLE``
and ``DETACHED_PROCESS`` defeat ``CREATE_NO_WINDOW``, so a caller passing
either is refused, on every platform, before anything starts. The measurements
behind this are in ``tests/test_no_window_flags.py``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

# What a silence report quotes back. Long enough to carry a stack trace's last
# line, short enough to sit inside a dashboard job's error field.
_REASON_TAIL = 300

# Win32 process creation flags, as numbers: the named constants exist on the
# subprocess module only on Windows, and this module is imported everywhere.
_CREATE_NO_WINDOW = 0x08000000
# The two that defeat it. Win32 ignores CREATE_NO_WINDOW beside either one. The
# first gives the child a new console, window included; the second gives it no
# console, so every console program the child starts gets a window of its own.
_WINDOW_OPENING = ((0x00000010, "CREATE_NEW_CONSOLE"),
                   (0x00000008, "DETACHED_PROCESS"))


class WindowFlagRefused(ValueError):
    """A caller asked for a creation flag that lets a child open a window.

    Raised by both wrappers before anything starts, on every platform, so the
    mistake fails on a developer's machine and in CI instead of as a window on
    an operator's desktop. Never stripped: a caller that asked for a new
    console expected one, and a hidden console handed over in silence would
    turn a design mistake into a mystery.
    """

    def __init__(self, refused: list[str], flags: int) -> None:
        self.refused = refused
        super().__init__(
            f"creationflags {flags:#010x} carries {' and '.join(refused)}, "
            "which lets a child open a console window, and cadre never opens "
            "one (issue #119). Leave it out: firm.core.proc keeps a child "
            "windowless on Windows.")


def _has_console() -> bool:
    """Is this process attached to a console right now?

    Asked at every spawn and never cached, because a process can gain or lose
    a console while it runs. Any failure to answer reads as no, because the
    caller then adds ``CREATE_NO_WINDOW``: a child that misses Ctrl+C is the
    lesser harm, and a window is the one the operator ruled out.
    """
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        attached = kernel32.GetConsoleProcessList
        attached.argtypes = (ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32)
        attached.restype = ctypes.c_uint32
        pids = (ctypes.c_uint32 * 64)()
        return attached(pids, 64) > 0
    except Exception:  # no answer is treated as no console, on purpose
        return False


def _window_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Refuse the flags that open a window; add ``CREATE_NO_WINDOW`` where a
    child would otherwise get one.

    The caller's other flags are kept. A caller that wants a hidden console of
    its own even when this process has one passes ``CREATE_NO_WINDOW`` itself,
    and it stays.
    """
    flags = kwargs.get("creationflags") or 0
    refused = [name for bit, name in _WINDOW_OPENING if flags & bit]
    if refused:
        raise WindowFlagRefused(refused, flags)
    if sys.platform == "win32" and not _has_console():
        kwargs["creationflags"] = flags | _CREATE_NO_WINDOW
    return kwargs


class NoOutput(RuntimeError):
    """A child whose output was the only signal it had exited saying nothing.

    Raised only by ``run_utf8(..., require_output=True)``. The message names
    the command, the exit code and whatever the child put on stderr, because a
    caller that catches this is about to report it to an operator and "no
    output" on its own tells them nothing they can act on.
    """


def _owned_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop every text/codec knob the caller may have passed.

    ``text=True`` beside an explicit ``encoding=`` is legal and harmless, but
    leaving a caller's ``encoding=`` in place would let a call site quietly opt
    out of the policy while still reading as though it were inside it.
    """
    for key in ("text", "universal_newlines", "encoding", "errors"):
        kwargs.pop(key, None)
    return kwargs


def _silence_reason(argv: Any, proc: subprocess.CompletedProcess[str]) -> str:
    """Why a required-output call is being failed, in the child's own words."""
    try:
        name = str(argv[0])
    except (IndexError, TypeError):
        name = str(argv)
    err = (proc.stderr or "").strip()
    last = err.splitlines()[-1][:_REASON_TAIL] if err else ""
    return (f"{name} exited {proc.returncode} without writing anything to "
            f"stdout, and its output was the only answer available"
            + (f" -- it said: {last}" if last else ""))


def run_utf8(argv: Any, *, require_output: bool = False,
             **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` with the child's output decoded as UTF-8.

    Every keyword ``subprocess.run`` takes is forwarded except the text and
    codec knobs, which this module owns (see ``_owned_kwargs``). The result is
    a text-mode ``CompletedProcess``: ``stdout`` and ``stderr`` are ``str``,
    exactly as they were under ``text=True``, minus the locale.

    ``require_output=True`` raises :class:`NoOutput` when the child exits with
    nothing on stdout. Use it only where the output IS the answer -- with it
    on, an empty stdout stops being a value the caller can silently read as
    zero, absent or clean.
    """
    proc = subprocess.run(argv, encoding="utf-8", errors="replace",
                          **_window_kwargs(_owned_kwargs(kwargs)))
    if require_output and not (proc.stdout or "").strip():
        raise NoOutput(_silence_reason(argv, proc))
    return proc


def popen_utf8(argv: Any, **kwargs: Any) -> subprocess.Popen[str]:
    """``subprocess.Popen`` with the child's streams decoded as UTF-8.

    This is the one that founding, wiring, the co-board briefing, Member
    spawns and the rail turn tap all go through: a long-lived ``claude
    --print`` whose stdout is iterated line by line. Under ``text=True`` that
    iteration is where #114 killed the run.
    """
    return subprocess.Popen(argv, encoding="utf-8", errors="replace",
                            **_window_kwargs(_owned_kwargs(kwargs)))
