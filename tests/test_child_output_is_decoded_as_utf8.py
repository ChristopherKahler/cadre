"""Cadre decodes a child's output as UTF-8, never as the machine's locale.

THE DEFECT (issue #114). ``text=True`` tells Python "this stream is text"; it
does not tell it WHICH text. Python then picks the decoder from the machine's
locale -- the ANSI code page on Windows, ASCII under ``LC_ALL=C`` on POSIX --
while every stream Cadre reads is UTF-8. Measured on Windows at main
496e6283, 2026-09-13:

    founding a firm through the hub   job 1349f14f6c35 -> status failed,
                                      "charmap codec can not decode byte 0x90
                                      in position 394", proposal null

    base_domain.rule_count            None, so the firm's base domain seed
                                      skipped and the firm was reported wired

Founding on Windows failed EVERY TIME. 0x90 is undefined in cp1252 and is the
third byte of every box-drawing character in U+25xx -- base's update banner is
drawn out of them, and a Claude JSON event stream carries an em dash or a
curly quote almost every run.

WHY THE ARMS RUN IN A CHILD PROCESS. The decoder is chosen when the stream is
opened, from the interpreter's locale, and pytest's own interpreter has
whatever the developer's shell gave it. The only way to measure the real
mechanism is to start a Python whose locale codec cannot carry UTF-8 and drive
Cadre's own functions inside it. ``LC_ALL=C`` alone is not enough: PEP 538
coerces the C locale to C.UTF-8 and PEP 540 may have UTF-8 mode on, so
``PYTHONCOERCECLOCALE=0`` and ``PYTHONUTF8=0`` go with it. On Windows none of
that matters -- the ANSI code page is cp1252 and the fault is reachable
exactly as the operator meets it.

AND WHY THERE IS A REACHABILITY PROBE rather than a platform mark. A host
whose default codec CAN carry these bytes (UTF-8 mode forced on, or the
Windows "Use Unicode UTF-8 worldwide" setting) cannot exhibit the defect at
all, and a red arm that passes there proves nothing.
``_decode_fault_is_reachable`` measures the mechanism itself -- it runs a real
child that writes a real box-drawing character through a real pipe and asks
whether ``text=True`` gets it back -- so the arms arm themselves on any host
that can actually show the fault, and skip where the premise is false rather
than where the platform name is wrong.

The sweep at the bottom is the part that keeps it fixed: 29 call sites across
15 files were reading child output with the locale codec, and a fix that
leaves the 30th to a future patch is a fix with a countdown on it.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
FIRM = SRC / "firm"

# U+2550 BOX DRAWINGS DOUBLE HORIZONTAL, which base's update banner is drawn
# out of. UTF-8: E2 95 90. 0x90 is UNDEFINED in cp1252 -- the exact byte the
# operator's founding run died on -- and 0xE2 is out of range for ASCII, so
# this one character reaches the fault on both of the hosts that have it.
BAR = "═"
BAR_BYTES = BAR.encode("utf-8")
assert BAR_BYTES == b"\xe2\x95\x90", "the byte under test is not the measured one"


# ---------------------------------------------------------------------------
# the environment that makes a POSIX host decode like a Windows one
# ---------------------------------------------------------------------------

def _locale_forced_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A child env whose default text codec cannot carry UTF-8.

    PYTHONIOENCODING is cleared for the reason tests/hooks/test_session_pulse_e2e.py
    already gives: a developer machine with it set hands the child a UTF-8
    stream for free and hides the whole defect.
    """
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.update({
        "LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C",
        "PYTHONCOERCECLOCALE": "0",   # PEP 538 would coerce C -> C.UTF-8
        "PYTHONUTF8": "0",            # PEP 540 UTF-8 mode would override both
        "PYTHONPATH": str(SRC),       # conftest: pin the tree under test
    })
    env.update(extra or {})
    return env


_REACHABILITY_PROBE = (
    "import subprocess, sys\n"
    "p = subprocess.run(\n"
    "    [sys.executable, '-c',\n"
    "     'import sys; sys.stdout.buffer.write(b\"\\\\xe2\\\\x95\\\\x90\")'],\n"
    "    capture_output=True, text=True)\n"
    "sys.stdout.write('GOT' if p.stdout else 'LOST')\n"
)


def _decode_fault_is_reachable() -> bool:
    """Does ``text=True`` lose a UTF-8 box character on this host?

    Measures the mechanism, not the platform. Two shapes count as the fault,
    because it presents differently on the two systems that have it:

      POSIX    the decode happens in the calling thread, so the probe dies
      Windows  it happens in Popen's reader thread, so run() RETURNS rc 0
               with stdout None and the probe prints LOST
    """
    try:
        p = subprocess.run([sys.executable, "-c", _REACHABILITY_PROBE],
                           capture_output=True, env=_locale_forced_env(),
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False   # cannot measure; treat the fault as unreachable
    if p.returncode != 0:
        return True
    return (p.stdout or b"").strip() != b"GOT"


FAULT_REACHABLE = _decode_fault_is_reachable()
unreachable = pytest.mark.skipif(
    not FAULT_REACHABLE,
    reason="this host's default codec carries UTF-8, so the locale decode "
           "fault cannot be reproduced here and a passing arm would prove "
           "nothing",
)


# ---------------------------------------------------------------------------
# a runnable stand-in that ignores its argv and writes exact bytes
# ---------------------------------------------------------------------------

def _fake_binary(tmp: Path, name: str, emit: bytes, rc: int = 0) -> Path:
    """A real executable file that writes *emit* to stdout and exits *rc*.

    It ignores every argument, which is the point: the callers under test
    build long argv (a founding prompt inlines two house documents) and the
    stand-in must not care what is in it.

    A ``/bin/sh`` wrapper rather than a shebang on POSIX, because a venv's
    interpreter path can exceed the 127-byte shebang limit and the failure
    then reads as "file not found" about a file that is right there. A
    ``.cmd`` on Windows, which is how subprocess runs npm and friends.
    """
    payload = tmp / f"{name}_emit.py"
    payload.write_text(
        "import sys\n"
        f"sys.stdout.buffer.write({emit!r})\n"
        "sys.stdout.buffer.flush()\n"
        f"raise SystemExit({rc})\n",
        encoding="utf-8")
    if os.name == "nt":
        exe = tmp / f"{name}.cmd"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{payload}"\r\n',
                       encoding="utf-8")
    else:
        exe = tmp / name
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{payload}"\n',
                       encoding="utf-8")
        exe.chmod(0o755)
    return exe


def _drive(code: str, tmp_path: Path) -> dict:
    """Run *code* in a locale-crippled child and read back its JSON line.

    The child prints with ``json.dumps`` defaults, so everything on the wire is
    ASCII escapes -- otherwise the child's own stdout, which is ASCII under
    this env, would raise UnicodeEncodeError while reporting a decode result
    and the two failures would be indistinguishable.
    """
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, env=_locale_forced_env(), cwd=str(tmp_path),
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert proc.returncode == 0, (
        "the child that drives cadre did not finish:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout[-2000:]}\n"
        f"stderr:\n{proc.stderr[-3000:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert lines, f"the child printed no result line:\n{proc.stdout[-2000:]}"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# ARM 1 -- the streamed read. This is the founding run that could not complete.
# ---------------------------------------------------------------------------
#
# THIS ARM DOES NOT FORCE THE LOCALE, and the reason is worth writing down
# because the obvious design does not work. Under `LC_ALL=C` a POSIX Python
# also encodes ARGV with the ascii codec, and `_FOUNDING_PROMPT` carries an em
# dash -- so the spawn dies in `_fork_exec` with UnicodeEncodeError before the
# child writes a byte. Windows has no such limit (argv is UTF-16), so the
# simulation breaks down for exactly the call this arm exists to drive.
#
# So this arm reaches the same line by the other door: a stream that is not
# clean UTF-8 EITHER. A lone 0x90 -- the byte the operator's run died on -- is
# not a valid UTF-8 sequence, and `text=True` decodes STRICT:
#
#   main, POSIX    utf-8 strict   -> UnicodeDecodeError mid-iteration
#   main, Windows  cp1252         -> charmap, byte 0x90, as measured
#   head, both     errors=replace -> one U+FFFD, and the founding completes
#
# which makes it red on main on every host, with no skip and no probe. The
# cp1252 mechanism itself is covered below, at the wrapper, where the argv is
# ASCII and the forced locale holds.

# Built with ensure_ascii=False on purpose: json.dumps escapes non-ASCII to
# \uXXXX by default, which would put a pure-ASCII stream on the wire and the
# arm would pass on main having reproduced nothing at all.
_PROPOSAL = json.dumps(
    {"firm_id": "wintest", "premise": f"a firm founded on Windows {BAR}"},
    ensure_ascii=False)
_ASSISTANT = json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text",
     "text": "Designing the operations. " * 14 + BAR * 3
             + " and on with the roster."}]}}, ensure_ascii=False).encode("utf-8")
# Spliced deep inside the line, not at its edge: the operator's failure was at
# byte 394 of a streamed line, and a reader that only ever meets bad input at a
# boundary is not being asked the same question.
_TAIL = " and on with the roster.".encode("utf-8")
assert _ASSISTANT.count(_TAIL) == 1
_ASSISTANT = _ASSISTANT.replace(_TAIL, b"\x90" + _TAIL, 1)

_STREAM = b"\n".join([
    json.dumps({"type": "system", "subtype": "init"}).encode("utf-8"),
    _ASSISTANT,
    json.dumps({"type": "result", "result": _PROPOSAL},
               ensure_ascii=False).encode("utf-8"),
]) + b"\n"
assert BAR_BYTES in _STREAM, "the stream carries no cp1252-undecodable byte"
assert b"\x90" + _TAIL in _STREAM, "the stream carries no invalid UTF-8 byte"


def test_a_founding_stream_that_is_not_clean_text_still_completes(
        tmp_path, monkeypatch):
    """RED ON MAIN, every host. The operator's failure, through the real code.

    ``_run_founding`` opens the agent's stdout and walks it line by line. That
    walk is where #114 killed every Windows founding: the decode raises, the
    broad ``except Exception`` below finishes the job as failed, and the Board
    is handed a founding that cannot be made to work.
    """
    from firm.dashboard import founding

    fake = _fake_binary(tmp_path, "claude", _STREAM)
    monkeypatch.setattr(founding, "resolve_claude_bin",
                        lambda: (str(fake), "fake claude for the decode arm"))
    # Kept small deliberately: the real _house_rules inlines two documents and
    # a 32k command line is a spawn failure on Windows that would mask the
    # decode this arm is about.
    monkeypatch.setattr(founding, "_house_rules", lambda: "(house rules elided)")
    monkeypatch.setattr(founding, "_inventory", lambda: ("(no arsenal)", {}))
    # _validate is a separate, separately tested concern.
    monkeypatch.setattr(founding, "_validate",
                        lambda proposal, inv=None: proposal)

    jid = "lapwing114"
    founding._jobs[jid] = {"status": "thinking", "narration": [],
                           "proposal": None, "error": None, "proc": None}
    try:
        founding._run_founding(
            jid, "A small winery that sells direct to restaurants.")
        job = dict(founding._jobs[jid])
    finally:
        founding._jobs.pop(jid, None)

    assert job["status"] == "ready", (
        f"founding did not complete: {job['error']!r} -- on main this is the "
        "strict/charmap decode of the agent's own stream")
    assert job["proposal"], "a ready job with no proposal is the #114 shape"
    assert BAR in json.dumps(job["proposal"], ensure_ascii=False), (
        "the box character did not survive the read into the proposal")


@unreachable
def test_text_true_really_does_lose_this_stream_on_this_host(tmp_path):
    """CONTROL. cp1252/ascii, the operator's actual mechanism, at the wrapper.

    Arm 1 reaches the fault through invalid UTF-8 because it has to. This one
    reaches it the way the operator did -- a locale whose codec has no byte
    0x90 -- on an ASCII argv where the forced locale holds. Without it, nothing
    in this file would show the cp1252 half going red.
    """
    fake = _fake_binary(tmp_path, "base", _BASE_RULES_OUT)
    code = (
        "import subprocess, json\n"
        f"p = subprocess.Popen([{str(fake)!r}], stdout=subprocess.PIPE,\n"
        "                     stderr=subprocess.PIPE, text=True)\n"
        "try:\n"
        "    n = sum(1 for _ in p.stdout)\n"
        "    print(json.dumps({'read': n, 'raised': ''}))\n"
        "except UnicodeDecodeError as exc:\n"
        "    print(json.dumps({'read': -1, 'raised': type(exc).__name__}))\n"
    )
    out = _drive(code, tmp_path)
    assert out["raised"] == "UnicodeDecodeError", (
        "text=True read a UTF-8 banner without complaint on a host the probe "
        f"called faulty -- the probe and the control disagree: {out!r}")


@unreachable
def test_popen_utf8_reads_the_same_stream_the_locale_cannot(tmp_path):
    """The positive twin of the control: the wrapper survives what killed it."""
    fake = _fake_binary(tmp_path, "base", _BASE_RULES_OUT)
    code = (
        "import json, subprocess\n"
        "from firm.core.proc import popen_utf8\n"
        f"p = popen_utf8([{str(fake)!r}], stdout=subprocess.PIPE,\n"
        "                stderr=subprocess.PIPE)\n"
        "text = p.stdout.read()\n"
        "p.wait()\n"
        "print(json.dumps({'bars': text.count('\\u2550'),\n"
        "                  'rules': '2 rules across both tiers' in text}))\n"
    )
    out = _drive(code, tmp_path)
    assert out["rules"] is True, f"the banner swallowed the answer: {out!r}"
    assert out["bars"] == 126, (
        f"read {out['bars']} box characters, expected 126 -- the decoder "
        "replaced or dropped what it should have carried")


# ---------------------------------------------------------------------------
# ARM 2 -- the one-shot read. This is the silence that was read as data.
# ---------------------------------------------------------------------------

# The shape base 0.15.x prints: the time-gated update banner, then the answer.
_BASE_RULES_OUT = (
    BAR * 63 + "\n"
    "  base update available\n"
    + BAR * 63 + "\n"
    "\n"
    "[zqdom] 2 rules across both tiers:\n"
    "  workspace 0. lapwing probe rule one\n"
    "  workspace 1. lapwing probe rule two\n"
    "  (global: none)\n"
).encode("utf-8")

_RULE_COUNT_CHILD = """
import json
from pathlib import Path
import firm.sysconfig.service as svc
from firm.services import base_domain

svc.which_base = lambda: {fake!r}
ws = Path({ws!r})
print(json.dumps({{"count": base_domain.rule_count(ws, "zqdom")}}))
"""


@unreachable
def test_a_rule_count_behind_a_utf8_banner_is_read_not_lost(tmp_path):
    """RED ON MAIN. The quieter half of #114, driven through the real reader.

    ``base rule list`` exits 0 for a populated domain, an empty one and a
    domain that never existed, so its OUTPUT is the only signal there is. On
    Windows the decode died in the reader thread, ``run`` returned rc 0 with
    stdout None, and ``rule_count`` answered None -- which ``_seed_rule`` then
    honoured by refusing to seed, and the firm was founded with a domain base
    drops whole.
    """
    ws = tmp_path / "firm"
    (ws / ".base").mkdir(parents=True)
    fake = _fake_binary(tmp_path, "base", _BASE_RULES_OUT)
    out = _drive(_RULE_COUNT_CHILD.format(fake=str(fake), ws=str(ws)), tmp_path)

    assert out["count"] == 2, (
        f"rule_count answered {out['count']!r} behind a UTF-8 banner; on main "
        "this is None on Windows and a UnicodeDecodeError on POSIX, and both "
        "reach the operator as 'the domain carries no rules'")


# ---------------------------------------------------------------------------
# a call that had to say something and said nothing is a failure with a reason
# ---------------------------------------------------------------------------

class _Silent:
    """rc 0, nothing on stdout -- what the dead reader thread used to leave."""

    stdout = ""
    stderr = ""
    returncode = 0


def _stub_base(tmp: Path) -> str:
    """A REAL file carrying this host's magic bytes, standing in for `base`.

    Never a bare string. tests/test_no_weak_base_stubs.py refuses those and its
    reason applies here exactly: a path that is not a file cannot tell REFUSED
    CORRECTLY apart from NEVER REACHED THE CHECK. Same shape as
    tests/services/test_writeback.py's own stub.
    """
    from firm.sysconfig.binaries import native_image_format

    magic = {"pe": b"MZ\x90\x00", "macho": b"\xcf\xfa\xed\xfe"}.get(
        native_image_format(), b"\x7fELF")
    binary = tmp / "stub-base"
    binary.write_bytes(magic + b"\x00" * 128)
    binary.chmod(0o755)
    return str(binary)


def test_silence_where_output_was_required_is_raised_not_returned():
    """``run_utf8(require_output=True)`` refuses to hand back a silence."""
    from firm.core.proc import NoOutput, run_utf8

    with pytest.raises(NoOutput) as caught:
        run_utf8([sys.executable, "-c", "pass"], capture_output=True,
                 require_output=True, timeout=60)
    assert "stdout" in str(caught.value), str(caught.value)


def test_an_unreadable_rule_set_is_not_reported_as_an_empty_one(monkeypatch,
                                                                tmp_path):
    """The reason travels, rather than being replaced by a guess.

    Before #114 every one of these ended at the same operator-facing sentence
    -- "carries no rules ... run firm doctor --fix" -- which is the wrong
    instruction for a domain nobody could read.
    """
    from firm.services import base_domain

    ws = tmp_path / "firm"
    (ws / ".base").mkdir(parents=True)
    base = _stub_base(tmp_path)
    monkeypatch.setattr("firm.sysconfig.service.which_base", lambda: base)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Silent())

    count, why = base_domain._rule_count(ws, "zqdom")
    assert count is None
    assert "stdout" in why, why

    seeded, seed_why = base_domain._seed_rule_detail(ws, "zqdom")
    assert seeded is False
    assert seed_why == why, "the reason must survive the layer, not be replaced"


def test_the_reason_reaches_the_line_the_operator_reads(monkeypatch, tmp_path):
    """``wire_workspace``'s detail names the cause AND keeps the route."""
    from firm.services import base_domain

    monkeypatch.setattr(base_domain, "scaffold_tier",
                        lambda ws: {"scaffolded": True, "detail": ""})
    monkeypatch.setattr(
        base_domain, "sync",
        lambda *a, **k: {"ok": True, "rule_seeded": False, "keywords": [],
                         "rule_reason": "base rule list exited 0 without "
                                        "writing anything to stdout"})
    res = base_domain.wire_workspace(tmp_path, "zqdom")

    assert res["live"] is False
    assert "without writing anything to stdout" in res["detail"], res["detail"]
    assert "doctor --fix" in res["detail"], "a report with no route gets retried"


def test_an_honestly_empty_rule_set_still_reads_as_empty(monkeypatch, tmp_path):
    """CONTROL for the test above: the old sentence is still the right one.

    A domain that genuinely carries no rules must not be reported as one that
    could not be read. Without this arm the change could have replaced one
    fixed sentence with another and nothing would have noticed.
    """
    from firm.services import base_domain

    monkeypatch.setattr(base_domain, "scaffold_tier",
                        lambda ws: {"scaffolded": True, "detail": ""})
    monkeypatch.setattr(
        base_domain, "sync",
        lambda *a, **k: {"ok": True, "rule_seeded": False, "keywords": [],
                         "rule_reason": ""})
    res = base_domain.wire_workspace(tmp_path, "zqdom")

    assert "carries no rules" in res["detail"], res["detail"]
    assert "could not be established" not in res["detail"]


# ---------------------------------------------------------------------------
# THE SWEEP -- what keeps the count at zero
# ---------------------------------------------------------------------------
#
# Parsed, never grepped. A substring search for `text=True` passes over a
# commented-out call and trips on a docstring, and the thing being asked --
# "does THIS call read a child's output" -- is a property of the call's
# keywords, which text does not carry.

_SUBPROCESS_READERS = {"run", "Popen", "check_output"}
_WRAPPERS = {"run_utf8", "popen_utf8"}
_UTF8_SPELLINGS = {"utf8"}


def _called_name(node: ast.Call) -> str | None:
    fn = node.func
    if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
            and fn.value.id == "subprocess"):
        return f"subprocess.{fn.attr}"
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _keyword(node: ast.Call, name: str) -> ast.keyword | None:
    return next((k for k in node.keywords if k.arg == name), None)


def _reads_child_output(node: ast.Call, name: str) -> bool:
    """Does this call capture what the child writes?

    ``check_output`` always does. Otherwise the call says so with
    ``capture_output=True`` or by wiring a stream to ``PIPE`` -- a stream sent
    to ``DEVNULL``, which is how the schedulers and the desktop launcher spawn,
    is thrown away and needs no decoder.

    A call carrying ``**kwargs`` counts as a reader whatever else it says: its
    stream configuration is decided somewhere else, so this file cannot see
    that it is safe, and "cannot see" must not resolve to "fine". That clause
    is what puts ``firm/core/proc.py``'s own two calls inside the sweep instead
    of quietly outside it.
    """
    if name == "subprocess.check_output":
        return True
    if any(k.arg is None for k in node.keywords):
        return True
    cap = _keyword(node, "capture_output")
    if cap is not None and isinstance(cap.value, ast.Constant) \
            and cap.value.value is True:
        return True
    for stream in ("stdout", "stderr"):
        k = _keyword(node, stream)
        if k is not None and isinstance(k.value, ast.Attribute) \
                and k.value.attr == "PIPE":
            return True
    return False


def _decodes_utf8(node: ast.Call) -> bool:
    """An explicit, READABLE ``encoding=``.

    A Name is not enough. ``encoding=ENCODING`` looks careful and this file
    cannot tell what it resolves to, so it does not count -- which is why
    ``firm/core/proc.py`` spells the literal out.
    """
    k = _keyword(node, "encoding")
    if k is None or not isinstance(k.value, ast.Constant):
        return False
    return str(k.value.value).lower().replace("-", "").replace("_", "") \
        in _UTF8_SPELLINGS


def _scan(source: str, where: str) -> tuple[list[str], int]:
    """(violations, output-reading call sites seen)."""
    bad: list[str] = []
    seen = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name in _WRAPPERS:
            seen += 1
            continue
        if name is None or not name.startswith("subprocess."):
            continue
        if name.split(".", 1)[1] not in _SUBPROCESS_READERS:
            continue
        if not _reads_child_output(node, name):
            continue
        seen += 1
        if not _decodes_utf8(node):
            bad.append(f"{where}:{node.lineno}: {name}(...) reads the child's "
                       "output with no explicit encoding")
    return bad, seen


def _sweep() -> tuple[list[str], int, int]:
    bad: list[str] = []
    seen = 0
    files = 0
    for path in sorted(FIRM.rglob("*.py")):
        files += 1
        found, n = _scan(path.read_text(encoding="utf-8"),
                         str(path.relative_to(REPO)))
        bad += found
        seen += n
    return bad, seen, files


_VIOLATING = (
    "import subprocess\n"
    "subprocess.run(['base', 'rule', 'list'], capture_output=True, text=True)\n"
)
_THROWN_AWAY = (
    "import subprocess\n"
    "subprocess.Popen(['x'], stdout=subprocess.DEVNULL,\n"
    "                 stderr=subprocess.DEVNULL)\n"
)
_FORWARDING = (
    "import subprocess\n"
    "def w(argv, **kw):\n"
    "    return subprocess.run(argv, **kw)\n"
)
_NAMED_CONSTANT = (
    "import subprocess\n"
    "ENC = 'utf-8'\n"
    "subprocess.run(['x'], capture_output=True, encoding=ENC)\n"
)


def test_the_sweep_can_go_red():
    """A checker nobody has seen fail is not a checker (falsify your prover)."""
    bad, seen = _scan(_VIOLATING, "canary")
    assert len(bad) == 1 and seen == 1, (bad, seen)

    bad, seen = _scan(_THROWN_AWAY, "canary")
    assert bad == [] and seen == 0, (
        "a DEVNULL spawn was counted as an output reader; the sweep would "
        "then demand a decoder for streams nobody reads")

    bad, seen = _scan(_FORWARDING, "canary")
    assert len(bad) == 1 and seen == 1, (
        "a call forwarding **kwargs was let through; that is exactly the "
        "shape a wrapper has, and a wrapper is where the whole policy lives")

    bad, seen = _scan(_NAMED_CONSTANT, "canary")
    assert len(bad) == 1 and seen == 1, (
        "encoding=<Name> was accepted; this file cannot resolve a name, so "
        "accepting one is accepting a call it has not read")


def test_no_call_in_src_reads_a_child_with_the_locale_codec():
    """THE COUNT IS ZERO, and this is what holds it there."""
    bad, seen, files = _sweep()
    assert bad == [], (
        f"{len(bad)} call site(s) read a child's output with whatever codec "
        "the machine's locale names. On Windows that is cp1252 and it takes "
        "down founding (#114). Route it through firm.core.proc, or pass "
        'encoding="utf-8" explicitly:\n  ' + "\n  ".join(bad))
    # A reader that reports zero must first prove it can see. The floor sits
    # below the count this found when it was written, so an honest refactor
    # does not fail it, and far above zero so an ast change that stops
    # matching does.
    assert seen >= 25, (
        f"the sweep found only {seen} output-reading call sites across {files} "
        "files, so it has stopped seeing them and the zero above means nothing")


def test_the_policy_lives_in_one_place():
    """Readers go through firm.core.proc, and the policy there is replacement.

    Not a style rule. The two wrappers are where ``errors="replace"`` is
    decided, and a call site that passes ``encoding="utf-8"`` by hand gets the
    decoder but not the replacement policy, so one stray byte still takes it
    down -- the defect again, with a different message.
    """
    from firm.core.proc import run_utf8

    # Behaviour, not a constant. A module constant spelled "replace" proves
    # nothing about what the call passes. 0x90 on its own is not valid UTF-8
    # at all, so strict errors would raise here and only replacement returns.
    proc = run_utf8(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\x90b')"],
        capture_output=True, timeout=60)
    assert proc.stdout == "a�b", f"run_utf8 returned {proc.stdout!r}"

    assert _sweep()[0] == []
    wrapper_calls = sum(
        1 for path in FIRM.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _called_name(node) in _WRAPPERS)
    assert wrapper_calls >= 25, (
        f"only {wrapper_calls} call sites go through firm.core.proc; the "
        "conversion moved 28, so the policy has leaked back out to the call "
        "sites")
