#!/usr/bin/env python3
"""End-to-end acceptance: can a real user install Cadre and USE it, on this OS?

Run it on Linux, macOS or Windows. It builds a wheel, installs it into an empty
virtual environment, and drives the actual user journey against the INSTALLED
package — never against the source tree. It prints a scoreboard, so a run that
is not all-green still tells you exactly how far the product gets today.

    python scripts/acceptance-e2e.py
    python scripts/acceptance-e2e.py --keep          # leave the sandbox behind
    python scripts/acceptance-e2e.py --json out.json # machine-readable summary

Every status is one of:

    PASS     the step did the thing and the evidence was read back
    FAIL     the step should work on this OS and did not
    BLOCKED  a known open defect stops it; the reason names the defect
    SKIP     not applicable on this OS

WHY THIS EXISTS, and why several steps look paranoid: on 2026-09-10 the CI
board had been red on macOS and Windows for a month while every local check
said green, because the Windows job ran a ``-k`` filter that deselected 1081 of
1321 tests and nobody compared the two numbers. Separately, an ad-hoc Windows
probe reported two product failures that were really a missing directory in the
probe's own copy of the tree. So this harness carries CONTROLS: steps that
prove the harness can see what it claims to be measuring, and negative arms
that must fail. A green step with no control is not evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IS_WIN = sys.platform.startswith("win")
TIMEOUT = 900

PASS, FAIL, BLOCKED, SKIP = "PASS", "FAIL", "BLOCKED", "SKIP"


class Board:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.t0 = time.time()

    def add(self, status: str, name: str, detail: str = "") -> str:
        self.rows.append({"status": status, "name": name, "detail": detail})
        mark = {PASS: "PASS   ", FAIL: "FAIL   ", BLOCKED: "BLOCKED", SKIP: "SKIP   "}[status]
        print(f"  {mark}  {name}")
        for line in str(detail).splitlines():
            if line.strip():
                print("           " + line[:150])
        return status

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r["status"] == status)

    def report(self) -> int:
        print()
        print("=" * 78)
        print("SCOREBOARD")
        print("=" * 78)
        for r in self.rows:
            print(f"  {r['status']:<8} {r['name']}")
        print("-" * 78)
        print(f"  {self.count(PASS)} pass · {self.count(FAIL)} fail · "
              f"{self.count(BLOCKED)} blocked · {self.count(SKIP)} skip "
              f"· {time.time() - self.t0:.0f}s")
        if self.count(FAIL):
            print()
            print("  NOT USABLE END TO END on this OS. The FAIL rows are "
                  "things that should work here and do not.")
        elif self.count(BLOCKED):
            print()
            print("  Usable as far as it goes, with known defects blocking the "
                  "BLOCKED rows. Each names its defect.")
        else:
            print()
            print("  USABLE END TO END on this OS.")
        return 1 if self.count(FAIL) else 0


def run(argv: list[str], *, cwd: Path | None = None, env: dict | None = None,
        timeout: int = TIMEOUT, stdin_text: str | None = None) -> tuple[int, str]:
    """Run a command with an EXPLICIT environment and a timeout.

    Explicit env, never ambient inheritance: a child spawned from a
    differently-launched parent inherits a different PATH and fails in ways
    that look like product bugs. Bytes decoded here rather than text=True,
    because text=True decodes as cp1252 on Windows and dies on any non-ASCII
    output.
    """
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        p = subprocess.run(
            argv, cwd=str(cwd) if cwd else None, env=e,
            capture_output=True, timeout=timeout,
            # Bytes, never text=True: on Windows text mode translates newlines
            # on the WRITE side, so a JSON payload arrives altered.
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
        )
    except FileNotFoundError as exc:
        return 127, f"not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    out = (p.stdout or b"").decode("utf-8", errors="replace")
    err = (p.stderr or b"").decode("utf-8", errors="replace")
    return p.returncode, out + err


def _registered_session_hook_command(settings: Path) -> str | None:
    """The SessionStart command string as `cadre init` actually wrote it.

    Read back from disk rather than reconstructed, because the point is to test
    what was wired, not what we believe was wired.
    """
    try:
        data = json.loads(settings.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    for entry in (data.get("hooks") or {}).get("SessionStart") or []:
        for h in entry.get("hooks") or [entry]:
            cmd = h.get("command") or ""
            if "cadre-session-pulse" in cmd:
                return cmd
    return None


def _expand_hook_command(command: str, ws: Path) -> list[str]:
    """Turn the registered command string into an argv, expanding the one
    variable Claude Code substitutes.

    `$CLAUDE_PROJECT_DIR` is expanded by the harness because Claude Code
    expands it at invocation; leaving it literal would test a path that does
    not exist and blame the hook for it. Nothing else is expanded — an
    interpreter name stays exactly as registered, which is the thing under test.
    """
    expanded = command.replace("$CLAUDE_PROJECT_DIR", str(ws))
    expanded = expanded.replace("${CLAUDE_PROJECT_DIR}", str(ws))
    return shlex.split(expanded, posix=not IS_WIN)


def venv_bin(venv: Path, name: str) -> Path:
    d = venv / ("Scripts" if IS_WIN else "bin")
    return d / (name + (".exe" if IS_WIN else ""))


def _make_output_utf8_safe() -> None:
    """Never let this harness die on its own report.

    The harness echoes child stdout, and `cadre doctor` prints U+2713. On
    Windows a redirected or piped stdout is cp1252, which cannot encode that
    character, so printing it raises UnicodeEncodeError and the harness exits 1
    — indistinguishable, to a reader, from the product failing.

    Measured on a real Windows host with PYTHONIOENCODING cleared: redirected
    stdout reports cp1252; `print("\u2713")` exits 1 with an EMPTY output file;
    after this call it exits 0 and writes E2 9C 93.

    errors="replace" rather than "strict": a stray undecodable byte in some
    child's output must not take the run down either. A mangled glyph in a
    report is a cosmetic loss; a dead harness is a lost measurement.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # Older Python, or a stream that is not a TextIOWrapper (pytest
            # capture, a pipe wrapper). Degrading to whatever the platform
            # gives us is correct; refusing to run is not.
            pass


def main() -> int:
    _make_output_utf8_safe()
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="leave the sandbox on disk for inspection")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--wheel", help="use this wheel instead of building one")
    a = ap.parse_args()

    b = Board()
    print("=" * 78)
    print("CADRE END-TO-END ACCEPTANCE")
    print("=" * 78)
    print(f"  os          {platform.platform()}")
    print(f"  sys.platform{'':<1}{sys.platform}")
    print(f"  python      {sys.version.split()[0]}  ({sys.executable})")
    print(f"  repo        {REPO}")
    print(f"  PYTHONIOENCODING={os.environ.get('PYTHONIOENCODING') or '(unset)'}")
    if os.environ.get("PYTHONIOENCODING"):
        print("  NOTE: PYTHONIOENCODING is set in this environment. It MASKS "
              "locale-encoding defects. Clear it for an honest run.")
    print()

    sandbox = Path(tempfile.mkdtemp(prefix="cadre-accept-"))
    venv = sandbox / "venv"
    ws = sandbox / "demo-firm"
    print(f"  sandbox     {sandbox}")
    print()

    try:
        # ---- 1. wheel ---------------------------------------------------
        if a.wheel:
            wheel = Path(a.wheel)
            b.add(PASS if wheel.is_file() else FAIL, "wheel provided",
                  str(wheel))
        else:
            dist = sandbox / "dist"
            rc, out = run([sys.executable, "-m", "pip", "install", "--quiet",
                           "--upgrade", "build"])
            rc, out = run([sys.executable, "-m", "build", "--outdir", str(dist)],
                          cwd=REPO)
            wheels = sorted(dist.glob("*.whl"))
            b.add(PASS if (rc == 0 and wheels) else FAIL,
                  "build a wheel from source",
                  f"rc={rc}\n" + ("\n".join(w.name for w in wheels)
                                  or out[-500:]))
            if not wheels:
                return b.report()
            wheel = wheels[0]

        # ---- 2. clean venv + install ------------------------------------
        rc, out = run([sys.executable, "-m", "venv", str(venv)])
        b.add(PASS if rc == 0 else FAIL, "create an empty virtual environment",
              f"rc={rc}\n{out[-300:]}")
        vpy = venv_bin(venv, "python")
        rc, out = run([str(vpy), "-m", "pip", "install", "--quiet",
                       "--upgrade", "pip"])
        rc, out = run([str(vpy), "-m", "pip", "install", str(wheel)])
        b.add(PASS if rc == 0 else FAIL, "pip install the wheel into it",
              f"rc={rc}\n{out[-600:]}")
        if rc != 0:
            return b.report()

        rc, freeze = run([str(vpy), "-m", "pip", "freeze"])
        mcp_line = next((l for l in freeze.splitlines()
                         if l.lower().startswith("mcp==")), "(mcp absent)")
        b.add(PASS if mcp_line.startswith("mcp==") else FAIL,
              "the mcp dependency resolved under its upper bound",
              mcp_line + "\n(an unbounded mcp resolved to a major version that "
                         "removed a module Cadre imports — issue #3)")

        # ---- 3. console scripts ----------------------------------------
        for exe in ("cadre", "firm"):
            rc, out = run([str(venv_bin(venv, exe)), "--version"])
            b.add(PASS if rc == 0 else FAIL, f"console script `{exe}` resolves",
                  f"rc={rc}  {out.strip()[:120]}")

        # ---- 4. imports, with a CONTROL that they come from the venv ----
        probe = (
            "import importlib, json, sys\n"
            "names=['firm','firm.mcp.tools','firm.pulse.spawn',"
            "'firm.dashboard.server','firm.services.base_domain']\n"
            "bad=[]\n"
            "for n in names:\n"
            "    try: importlib.import_module(n)\n"
            "    except Exception as e: bad.append(n+': '+repr(e))\n"
            "import firm\n"
            "print(json.dumps({'bad':bad,'file':firm.__file__}))\n"
        )
        rc, out = run([str(vpy), "-c", probe], cwd=sandbox)
        try:
            got = json.loads(out.strip().splitlines()[-1])
        except Exception:
            got = {"bad": ["probe produced no JSON: " + out[-300:]], "file": ""}
        b.add(PASS if (rc == 0 and not got["bad"]) else FAIL,
              "every module imports from the installed package",
              "\n".join(got["bad"]) or "all five imported")
        # CONTROL: without this, the whole run could be exercising the repo.
        from_venv = str(venv).lower() in got.get("file", "").lower()
        b.add(PASS if from_venv else FAIL,
              "CONTROL: firm resolves from the venv, not from the source tree",
              f"firm.__file__ = {got.get('file')}\n"
              + ("" if from_venv else
                 "the harness is testing the SOURCE TREE; every result above "
                 "is about the repo, not about what a user installs"))

        rc, out = run([str(vpy), "-c",
                       "from firm.mcp.tools import mcp;"
                       "print(len(mcp._tool_manager._tools))"], cwd=sandbox)
        b.add(PASS if rc == 0 and out.strip().isdigit() else FAIL,
              "the MCP tool surface loads from the installed package",
              f"rc={rc}  tools={out.strip()[:40]}")

        # ---- 5. package DATA, separate from code -----------------------
        data_probe = (
            "import pathlib, firm, json\n"
            "r=pathlib.Path(firm.__file__).parent\n"
            "print(json.dumps({k:len(sorted(r.glob(g))) for k,g in "
            "[('migrations','migrations/*.sql'),"
            "('dashboard','dashboard/*.html'),"
            "('templates','templates/**/*.md')]}))\n"
        )
        rc, out = run([str(vpy), "-c", data_probe], cwd=sandbox)
        try:
            counts = json.loads(out.strip().splitlines()[-1])
        except Exception:
            counts = {}
        missing = [k for k, v in counts.items() if not v]
        b.add(PASS if counts and not missing else FAIL,
              "the wheel carries its data files, not only its code",
              json.dumps(counts) + ("" if not missing else
                                    f"\nmissing: {missing} — a wheel can import "
                                    "perfectly and still ship no migrations"))

        # ---- 6. cadre init, with a CONTROL that the db was absent -------
        ws.mkdir(parents=True, exist_ok=True)
        db = ws / ".firm" / "firm.db"
        b.add(PASS if not db.exists() else FAIL,
              "CONTROL: no firm database exists before init",
              f"{db} absent" if not db.exists() else
              "a database already exists, so 'init created it' proves nothing")
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws), "--demo"])
        b.add(PASS if rc == 0 and db.is_file() else FAIL,
              "cadre init --demo creates a firm",
              f"rc={rc}\n{out[-700:]}")

        if db.is_file():
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            # Count applied migrations off sqlite_master rather than a
            # guessed table name: an earlier version queried
            # schema_migrations, which does not exist, and reported
            # "migrations=None" as if that were a finding.
            try:
                mig = c.execute(
                    "select count(*) from sqlite_master where type='table'"
                ).fetchone()[0]
            except sqlite3.Error:
                mig = None
            try:
                mem = c.execute("select count(*) from member").fetchone()[0]
                uni = c.execute("select count(*) from unit").fetchone()[0]
            except sqlite3.Error:
                mem = uni = 0
            c.close()
            b.add(PASS if mem > 0 else FAIL,
                  "the demo firm actually has a roster and work",
                  f"members={mem} units={uni} migrations={mig}")

        # ---- 7. hooks + idempotence ------------------------------------
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws),
                       "--install-hooks"])
        hook = ws / ".claude" / "hooks" / "cadre-session-pulse.py"
        settings = ws / ".claude" / "settings.json"
        wired = settings.is_file() and "cadre-session-pulse" in \
            settings.read_text(encoding="utf-8", errors="replace")
        b.add(PASS if rc == 0 and hook.is_file() and wired else FAIL,
              "cadre init --install-hooks wires the session hook",
              f"rc={rc}  hook={hook.is_file()}  registered={wired}")

        # Registered is NOT running. The two checks above both pass on a hook
        # that can never execute, and on 2026-09-10 the shipped hook could not:
        # install_hooks.py registers it under bare `python3`, which is not the
        # interpreter that has `firm`. It exits 0, prints nothing, and its
        # contract says silence means "no firm here" — so the roster never
        # reaches a session and nothing reports it.
        #
        # This runs the command AS REGISTERED, taken out of settings.json
        # rather than chosen here. An interpreter of the harness's own picking
        # would prove the hook script works while saying nothing about what
        # `cadre init` actually wired, which is the whole defect.
        registered = _registered_session_hook_command(settings)
        if not registered:
            b.add(FAIL, "the session hook command can be read back out of "
                        "settings.json",
                  "no SessionStart command found; cannot test what was wired")
        else:
            payload = json.dumps({"cwd": str(ws)})
            argv = _expand_hook_command(registered, ws)
            rc_h, out_h = run(argv, cwd=ws, stdin_text=payload)
            got_roster = "active-roster" in out_h or "member" in out_h.lower()
            b.add(PASS if got_roster else FAIL,
                  "the session hook AS REGISTERED actually emits the roster",
                  f"command: {registered}\nargv: {argv}\nrc={rc_h} "
                  f"stdout={len(out_h)} bytes\n"
                  + (out_h[:300] if got_roster else
                     "EMPTY OR NO ROSTER. The hook exits 0 and says nothing, "
                     "which its contract reads as 'no firm here'. A session "
                     "gets no roster and nothing reports it. Check that the "
                     "registered interpreter is the one that has `firm`."))

            # RED ARM. Without one, a green row above could mean the harness
            # cannot tell a working hook from a broken one.
            #
            # This arm used to run the hook under `-s` and require no roster,
            # on the reasoning that an interpreter without cadre cannot serve
            # one. That stopped being true when the interpreter fix landed: the
            # hook now reads the path recorded in `.firm/python-path`, puts it
            # on sys.path itself, and serves the roster whichever interpreter
            # Claude Code invoked. The old arm went FAIL on main while the row
            # above went PASS -- the harness calling a fix a defect.
            #
            # So the discriminator is the recorded path, and the two arms now
            # differ by exactly one thing. Remove the marker and a firm that IS
            # here must go silent on stdout and LOUD on stderr, which is the
            # third state the fix introduced. User site stays suppressed in
            # both arms: without that, bare python3 on a developer box reaches
            # `firm` through an editable-install .pth no matter what the marker
            # says, and this arm would pass for the wrong reason.
            marker = ws / ".firm" / "python-path"
            saved = marker.read_bytes() if marker.is_file() else None
            if saved is None:
                b.add(FAIL, "RED ARM: the recorded interpreter path exists to "
                            "be removed",
                      f"{marker} is not a file, so the arm below would prove "
                      "nothing. cadre init --install-hooks is supposed to "
                      "record it.")
            else:
                try:
                    marker.unlink()
                    rc_b, out_b = run(argv, cwd=ws, stdin_text=payload,
                                      env={"PYTHONNOUSERSITE": "1",
                                           "FIRM_SRC": ""})
                finally:
                    marker.write_bytes(saved)
                quiet = "active-roster" not in out_b
                loud = "could not be imported" in out_b and "paths tried" in out_b
                b.add(PASS if quiet and loud else FAIL,
                      "RED ARM: with the recorded path removed the hook goes "
                      "quiet and says why",
                      f"rc={rc_b} output={len(out_b)} bytes\n"
                      + ("correctly emitted no roster and named the "
                         "interpreter and the paths it tried"
                         if quiet and loud else
                         ("it STILL emitted a roster, so the row above cannot "
                          "distinguish a working hook from a broken one"
                          if not quiet else
                          "it emitted no roster but said nothing about why. "
                          "That is the silent-success failure the fix exists "
                          "to remove: an operator sees the same nothing as an "
                          "empty directory.")))

            # Green above is still not proof a USER gets a roster. On a
            # developer box bare `python3` reaches `firm` through an
            # editable-install .pth in USER site-packages, and it resolves to
            # the SOURCE TREE, not the installed wheel. Measured 2026-09-10:
            # bare python3 imported firm from src/, while both `-s` and
            # PYTHONNOUSERSITE=1 raised ModuleNotFoundError. Suppressing user
            # site-packages removes the accident and leaves what a fresh user
            # actually has, so this arm separates "works here" from "works".
            rc_u, out_u = run(argv, cwd=ws, stdin_text=payload,
                              env={"PYTHONNOUSERSITE": "1"})
            got_u = "active-roster" in out_u or "member" in out_u.lower()
            b.add(PASS if got_u else FAIL,
                  "the registered hook emits the roster without a developer "
                  "editable install helping it",
                  f"command: {registered}\nrc={rc_u} "
                  f"stdout={len(out_u)} bytes\n"
                  + (out_u[:300] if got_u else
                     "NO ROSTER once user site-packages is suppressed. The row "
                     "above is green only because THIS machine carries an "
                     "editable-install .pth that a user does not have. The "
                     "registered interpreter cannot import cadre for anyone "
                     "else, so the hook exits 0 and prints nothing, and its "
                     "contract reads that silence as 'no firm here'. Record "
                     "the interpreter path at install time and have the hook "
                     "read it."))
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws), "--demo"])
        again_ok = rc == 0 and ("skip" in out.lower() or "already" in out.lower())
        b.add(PASS if again_ok else FAIL,
              "re-running init is idempotent, not destructive",
              f"rc={rc}\n{out[-400:]}")

        # ---- 8. the CLI journey a Member and the Board actually drive ---
        firm_exe = str(venv_bin(venv, "firm"))
        member = None
        if db.is_file():
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                member = c.execute("select id from member limit 1").fetchone()[0]
                unit = c.execute("select id from unit limit 1").fetchone()[0]
            except sqlite3.Error:
                unit = None
            c.close()

        if not member:
            b.add(FAIL, "a member exists to act as", "no member row to drive")
        else:
            env = {"CADRE_MEMBER_ID": member}
            rc, out = run([firm_exe, "gate", "request",
                           "--action", "acceptance harness: prove the gate path",
                           "--target-type", "unit", "--target-id", str(unit),
                           "--context", "written by scripts/acceptance-e2e.py"],
                          cwd=ws, env=env)
            # Read the row BACK. rc 0 alone does not prove a row landed.
            gate_row = None
            if db.is_file():
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                c.row_factory = sqlite3.Row
                try:
                    r = c.execute("select * from gate order by rowid desc "
                                  "limit 1").fetchone()
                    gate_row = dict(r) if r else None
                except sqlite3.Error:
                    pass
                c.close()
            ok = (rc == 0 and gate_row
                  and gate_row.get("requesting_member_id") == member
                  and gate_row.get("status") == "pending")
            b.add(PASS if ok else FAIL,
                  "a Member can ask the Board for approval (firm gate request)",
                  f"rc={rc}\n"
                  + (f"gate={gate_row.get('id')} status={gate_row.get('status')} "
                     f"by={gate_row.get('requesting_member_id')}"
                     if gate_row else "NO GATE ROW LANDED\n" + out[-300:]))

            rc, out = run([firm_exe, "escalation", "raise",
                           "--title", "acceptance harness: prove the escalation path"],
                          cwd=ws, env=env)
            esc_ok = rc == 0
            if db.is_file() and esc_ok:
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    esc_ok = c.execute(
                        "select count(*) from escalation where title like "
                        "'acceptance harness%'").fetchone()[0] > 0
                except sqlite3.Error:
                    esc_ok = False
                c.close()
            b.add(PASS if esc_ok else FAIL,
                  "a Member can escalate to the Board (firm escalation raise)",
                  f"rc={rc}\n{out[-300:] if not esc_ok else 'row read back'}")

        # ---- 9. the host scheduler for THIS OS -------------------------
        sched_probe = (
            "import json\n"
            "from firm.sched import resolve_scheduler\n"
            "s=resolve_scheduler(); ok,d=s.available()\n"
            "print(json.dumps({'backend':type(s).__name__,'ok':bool(ok),"
            "'detail':str(d)[:200]}))\n"
        )
        rc, out = run([str(vpy), "-c", sched_probe], cwd=sandbox)
        try:
            sc = json.loads(out.strip().splitlines()[-1])
        except Exception:
            sc = {"backend": "?", "ok": False, "detail": out[-200:]}
        b.add(PASS if sc["ok"] else FAIL,
              "the host scheduler for this OS resolves and is available",
              f"backend={sc['backend']} available={sc['ok']} {sc['detail']}")

        # ---- 10. spawn: the step that has never run off Linux ----------
        # Negative arm FIRST. If a bogus binary "spawns fine", the positive
        # arm below proves nothing at all.
        bogus = str(sandbox / "definitely-not-here")
        spawn_probe = (
            "import json,sys\n"
            "from firm.pulse.spawn import resolve_claude_bin\n"
            "p,d=resolve_claude_bin()\n"
            "print(json.dumps({'path':p,'detail':d[:200]}))\n"
        )
        rc, out = run([str(vpy), "-c", spawn_probe], cwd=sandbox,
                      env={"CADRE_CLAUDE_BIN": bogus})
        try:
            neg = json.loads(out.strip().splitlines()[-1])
        except Exception:
            neg = {"path": "?", "detail": out[-200:]}
        b.add(PASS if neg["path"] is None else FAIL,
              "NEGATIVE CONTROL: a nonexistent runtime is refused",
              f"resolved={neg['path']}\n{neg['detail']}")

        native = (os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe") \
            if IS_WIN else "/bin/echo"
        rc, out = run([str(vpy), "-c", spawn_probe], cwd=sandbox,
                      env={"CADRE_CLAUDE_BIN": native})
        try:
            pos = json.loads(out.strip().splitlines()[-1])
        except Exception:
            pos = {"path": None, "detail": out[-200:]}
        if pos["path"]:
            b.add(PASS, "a native executable is accepted as the Member runtime",
                  f"{native} -> accepted")
        else:
            b.add(BLOCKED,
                  "a native executable is accepted as the Member runtime",
                  f"{native} REFUSED: {pos['detail']}\n"
                  "KNOWN DEFECT: _is_execable in firm/pulse/spawn.py accepts "
                  "only ELF (\\x7fELF) or a shebang, so PE (Windows) and Mach-O "
                  "(macOS) binaries are rejected and no Member can spawn. "
                  "Nothing below this line can run until it is fixed.")

        # ---- 11. base as the engine, via the extension -----------------
        rc, out = run(["base", "--version"])
        if rc != 0:
            b.add(SKIP, "base is installed on this host",
                  "base not on PATH; the extension journey cannot be checked "
                  "here. This is a host setup fact, not a Cadre defect.")
        else:
            b.add(PASS, "base is installed on this host", out.strip()[:80])
            rc2, out2 = run([str(vpy), "-c",
                             "import firm.services.base_domain as m;"
                             "print(getattr(m,'__file__',''))"], cwd=sandbox)
            b.add(PASS if rc2 == 0 else FAIL,
                  "the base bridge module loads from the installed package",
                  out2.strip()[:160])
            b.add(BLOCKED, "base is the engine for this firm (extension wired)",
                  "PENDING: the extension install path and its verbs "
                  "(base cadre learn / base cadre complete, the write-back "
                  "marker and the blocking stop hook) are on lane/"
                  "base-extension and not yet merged to main. This row turns "
                  "green when that lands and this harness is taught its exact "
                  "command sequence.")

        return b.report()
    finally:
        if a.json_out:
            Path(a.json_out).write_text(json.dumps({
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "pythonioencoding": os.environ.get("PYTHONIOENCODING"),
                "rows": b.rows,
                "pass": b.count(PASS), "fail": b.count(FAIL),
                "blocked": b.count(BLOCKED), "skip": b.count(SKIP),
            }, indent=2), encoding="utf-8")
            print(f"\n  json summary -> {a.json_out}")
        if a.keep:
            print(f"\n  sandbox kept at {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
