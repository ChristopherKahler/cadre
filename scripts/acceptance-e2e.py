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
import hashlib
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


def operator_digest() -> str:
    """One hash over every operator file this run could contaminate.

    Content hash and never mtime: `fs::copy` preserves mtime, so a harness
    keyed on it reports "unchanged" while writing.

    It watches the graph AND the workspace registry, and on WSL BOTH platform
    tiers. It began as a hash of the graph alone and passed while the run was
    appending junk workspaces to the operator's REGISTRY, which is a different
    file; and the Linux tier read clean through a run whose writes were landing
    in the Windows one across /mnt/c. One file is not isolation and one tier is
    not either.
    """
    home = Path.home()
    watched = [home / ".base" / "graph.nq",
               home / ".base-gbl" / ".base" / "graph.nq",
               home / ".base-gbl" / "base.toml",
               Path("/mnt/c/Users") / home.name / ".base-gbl" / "base.toml"]
    parts = []
    for f in watched:
        try:
            parts.append(f.name + ":" + hashlib.md5(f.read_bytes()).hexdigest())
        except OSError:
            parts.append(f.name + ":absent")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


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

    # Opened HERE, before the first command, and closed in the last row.
    #
    # It used to be taken inside the base section, hundreds of lines below the
    # `cadre init` calls it needed to watch, so it could only ever see its own
    # section. Widening WHAT it hashed would not have helped: the window itself
    # was in the wrong place, and the contamination happened before it opened.
    run_start_digest = operator_digest()

    sandbox = Path(tempfile.mkdtemp(prefix="cadre-accept-"))

    # `cadre init` scaffolds the firm's BASE tier, so it shells out to whatever
    # base this host carries. Before that landed these calls were inert; now an
    # un-isolated one registers this throwaway sandbox in the OPERATOR's global
    # workspace registry, which `base workspace sync` then copies into the
    # CLAUDE.md they load in every session. Measured: six `cadre-accept-*`
    # entries from three runs, on both platforms.
    #
    # CADRE_NO_BASE is the load-bearing half. BASE_HOME alone does not hold on
    # WSL, where `base` resolves to the WINDOWS binary across /mnt/c and a POSIX
    # BASE_HOME neither redirects its write nor trips base's own isolation
    # panic. Not finding a binary at all is the only thing that reliably stops a
    # child process.
    SANDBOX_ENV = {"CADRE_NO_BASE": "1",
                   "BASE_HOME": str(sandbox / "base-home")}
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
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws), "--demo"],
                      env=SANDBOX_ENV)
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
                       "--install-hooks"], env=SANDBOX_ENV)
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
        rc, out = run([str(venv_bin(venv, "cadre")), "init", str(ws), "--demo"],
                      env=SANDBOX_ENV)
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
            # ---- base is the engine: one row per assertion ----------
            #
            # Not one "base is the engine" row. Eleven, because when this goes
            # red the operator needs the step that broke, not the news that
            # something in a ten-command journey did. Each has its control
            # beside it: the negative arm that fails if the check has gone
            # blind. Measured end to end on Windows first; the sequence is
            # .base-gbl/docs/2026-09-10-godwit-base-engine-journey.md.
            #
            # TWO TRAPS PAID FOR ALREADY, both live in these four lines.
            #
            # `sandbox` is tempfile.mkdtemp(), which on Windows lands under
            # AppData\Local\Temp — INSIDE the user profile. base's own write
            # tripwire panics there, because `dirs` never consults $HOME on
            # Windows and cannot tell a fake tier under the profile from the
            # real one. So this section takes a drive-root BASE_HOME instead.
            #
            # And BASE_HOME is only half of it: it governs the GLOBAL tier.
            # The WORKSPACE tier follows the CURRENT DIRECTORY. Set BASE_HOME,
            # run from the wrong cwd, and base reads and writes the operator's
            # own graph while every assertion here still passes. Every base
            # call below therefore passes cwd=ws.
            base_home = (Path(Path(sys.executable).drive + "/cadre-accept-base")
                         if IS_WIN else Path(tempfile.gettempdir()) / "cadre-accept-base")
            shutil.rmtree(base_home, ignore_errors=True)
            base_home.mkdir(parents=True, exist_ok=True)
            benv = {"BASE_HOME": str(base_home), "PYTHONIOENCODING": "utf-8"}

            def bmember(mid: str | None) -> dict:
                e = dict(benv)
                e["CADRE_MEMBER_ID"] = mid or ""
                return e

            before_hash = run_start_digest

            # 1. Isolation. Nothing else in this section may run until base is
            #    demonstrably reading the FIRM's tier, because if it is reading
            #    the operator's, every row below is writing their knowledge.
            rc, out = run(["base", "scaffold", "."], cwd=ws, env=benv)
            rc, out = run(["base", "doctor"], cwd=ws, env=benv)
            reads_firm = str(ws) in out.replace("/", os.sep)
            b.add(PASS if reads_firm else FAIL,
                  "base reads the firm's own workspace tier, not the operator's",
                  (f"tier under {ws}" if reads_firm else
                   "base did NOT name the firm's directory. BASE_HOME governs "
                   "the global tier only; the workspace tier follows cwd. "
                   "Refusing to run the rest of this section."))
            if not reads_firm:
                return b.report()

            # 2. The extension, through the shipped verb rather than a
            #    python -c. `read_back` is the claim that matters: install
            #    re-reads the landed file instead of trusting its own write,
            #    so "ok" alone would pass over a manifest that never landed.
            rc, out = run([str(venv_bin(venv, "cadre")), "extension", "install"],
                          cwd=ws, env=benv)
            landed = base_home / ".base-gbl" / "extensions" / "cadre.toml"
            wired = rc == 0 and "read back" in out and landed.exists()
            b.add(PASS if wired else FAIL,
                  "the Cadre manifest installs into base and is read back",
                  out.strip()[:160] if wired else
                  f"rc={rc} landed={landed.exists()} :: {out.strip()[:200]}")

            # 2b. THE ROW ABOVE CANNOT FAIL IN THE WAY THAT MATTERS, which is
            #     the whole reason this one exists. `rc == 0`, the words "read
            #     back" in the output, and a file on disk were ALL THREE TRUE
            #     at d842529 while `base cadre` exited 127 on both platforms.
            #     Measured, not reasoned about. An install reporting success
            #     over a dead command IS F1, and nothing short of running the
            #     handler can tell the two apart.
            #
            #     Note what this invokes: `base cadre`, the extension handler.
            #     Every other cadre call in this section goes through the venv
            #     console script, which reaches the CLI without ever testing
            #     the manifest that is supposed to point at it.
            rc, out = run(["base", "cadre", "--version"], cwd=ws, env=benv)
            runs = rc == 0 and "cadre" in out.lower()
            b.add(PASS if runs else FAIL,
                  "the extension's command RUNS through base, not merely installs",
                  f"`base cadre --version` -> {out.strip()[:120]}" if runs else
                  f"rc={rc} :: {out.strip()[:200]} :: the manifest is installed "
                  "and the command is dead — this is the F1 shape")

            # 3. A generated domain block is not a live wire. base drops a
            #    MATCHED domain carrying zero rules, silently and totally, so
            #    the block existing proves nothing. The rule count is the
            #    assertion; the block is not.
            rc, out = run([str(vpy), "-c",
                           "import sqlite3;"
                           "from pathlib import Path;"
                           "from firm.services import base_domain;"
                           "c = sqlite3.connect('.firm/firm.db');"
                           "c.row_factory = sqlite3.Row;"
                           "r = base_domain.sync(Path('.').resolve(), "
                           "__import__('firm.core.db', fromlist=['x'])"
                           ".resolve_firm_id(c, None), conn=c);"
                           "c.close(); print(r)"], cwd=ws, env=benv)
            firm_id = None
            try:
                with sqlite3.connect(f"file:{ws / '.firm' / 'firm.db'}?mode=ro",
                                     uri=True) as c:
                    firm_id = c.execute("select id from firm limit 1").fetchone()[0]
            except sqlite3.Error:
                pass
            rc2, out2 = run(["base", "rule", "list", "--domain", str(firm_id)],
                            cwd=ws, env=benv)
            live_rule = " 0 rules" not in out2 and "rules" in out2
            b.add(PASS if live_rule else FAIL,
                  "the firm's base domain carries a live rule",
                  out2.strip().splitlines()[0][:140] if out2.strip() else
                  "no rule count came back; base drops a domain with zero rules "
                  "whole, so the block can be perfect and inject nothing")

            # 4/5. Per-member scope, and the control that makes "different"
            #      mean something. Two Members must see different briefings AND
            #      a Board session must see none — without the second, two
            #      arbitrary strings would pass.
            members: list[str] = []
            try:
                with sqlite3.connect(f"file:{ws / '.firm' / 'firm.db'}?mode=ro",
                                     uri=True) as c:
                    members = [r[0] for r in
                               c.execute("select id from member order by id")]
            except sqlite3.Error:
                pass
            if len(members) < 2:
                b.add(SKIP, "two Members get different briefings",
                      f"the demo firm has {len(members)} member(s); this row "
                      "needs two and is not asserting anything")
            else:
                one, two = members[0], members[1]
                # The demo firm's unit ids are not this harness's to assume.
                # Read the first one, give it to Member one, and mint a second
                # for Member two so the two briefings have different content
                # to differ ABOUT — two empty briefings are also "different".
                _, uout = run([str(vpy), "-c",
                               "import sqlite3, sys;"
                               "c = sqlite3.connect('.firm/firm.db');"
                               "u = [r[0] for r in c.execute("
                               "'select id from unit order by id')];"
                               "c.execute('update unit set assignee_member_id=?"
                               " where id=?', (sys.argv[1], u[0]));"
                               "c.execute(\"insert into unit (id, firm_id, name,"
                               " project_id, assignee_member_id, status) select"
                               " 'ACC-U2', firm_id, 'second unit', project_id,"
                               " ?, 'in_progress' from unit where id=?\","
                               " (sys.argv[2], u[0]));"
                               "c.commit(); c.close(); print(u[0])",
                               one, two], cwd=ws, env=benv)
                unit_one = (uout.strip().splitlines() or [""])[-1].strip()
                if not unit_one:
                    b.add(FAIL, "the demo firm has a Unit to close",
                          "no unit id came back, so the gate rows below would "
                          "have asserted nothing")
                    return b.report()
                _, b1 = run([str(venv_bin(venv, "cadre")), "brief"],
                            cwd=ws, env=bmember(one))
                _, b2 = run([str(venv_bin(venv, "cadre")), "brief"],
                            cwd=ws, env=bmember(two))
                differ = bool(b1.strip()) and bool(b2.strip()) and b1 != b2
                b.add(PASS if differ else FAIL,
                      "two Members of one firm get different briefings",
                      f"{one}: {b1.strip().splitlines()[0][:60] if b1.strip() else '(empty)'} | "
                      f"{two}: {b2.strip().splitlines()[0][:60] if b2.strip() else '(empty)'}")

                _, b0 = run([str(venv_bin(venv, "cadre")), "brief"],
                            cwd=ws, env=bmember(None))
                b.add(PASS if not b0.strip() else FAIL,
                      "a Board session is briefed as nobody (scope control)",
                      "silent, which is correct — the Board is not a Member"
                      if not b0.strip() else
                      "a Board session was handed a Member's queue: "
                      + b0.strip()[:120])

                # 6-8. The gate: armed, biting, and not biting the wrong person.
                run([str(vpy), "-c",
                     "from pathlib import Path;"
                     "from firm.cli.install_hooks import install_writeback_hook;"
                     "install_writeback_hook(Path('.').resolve())"],
                    cwd=ws, env=benv)
                gate = ws / ".claude" / "hooks" / "cadre-writeback-gate.py"

                def fire_gate(mid: str) -> int:
                    payload = json.dumps({"cwd": str(ws), "stop_hook_active": False})
                    p = subprocess.run([str(vpy), str(gate)], cwd=str(ws),
                                       env={**os.environ, **bmember(mid)},
                                       input=payload.encode(),
                                       capture_output=True, timeout=120)
                    return p.returncode

                b.add(PASS if fire_gate(one) == 0 else FAIL,
                      "the write-back gate lets a Member with nothing owed finish",
                      "exit 0 with no debt")

                run([str(venv_bin(venv, "cadre")), "member", "grant",
                     "authority", one, "--comment", "acceptance"],
                    cwd=ws, env=benv)
                rc, out = run([str(venv_bin(venv, "cadre")), "complete", unit_one],
                              cwd=ws, env=bmember(one))
                blocked = fire_gate(one)
                b.add(PASS if blocked == 2 else FAIL,
                      "closing a Unit with nothing recorded blocks the session",
                      f"gate exit {blocked} (want 2) after: {out.strip()[:120]}")

                b.add(PASS if fire_gate(two) == 0 else FAIL,
                      "the other Member is not blocked by that debt (gate control)",
                      "exit 0 — a gate that blocks everybody passes the row "
                      "above and makes the framework unusable")

                # 9/10. The graph, read empty BEFORE so a note found AFTER is
                #       known to be this run's. Without the before-arm, a note
                #       from any earlier run proves nothing.
                _, empty = run(["base", "learn", "--list", "--domain",
                                str(firm_id)], cwd=ws, env=benv)
                was_empty = "No notes" in empty
                b.add(PASS if was_empty else FAIL,
                      "the firm's graph starts empty (evidence control)",
                      "no notes yet" if was_empty else
                      "the graph was NOT empty first, so a lesson found after "
                      "the write-back proves nothing about who put it there")

                lesson = ("The acceptance run taught that the gate and the "
                          "command are one piece.")
                rc, out = run([str(venv_bin(venv, "cadre")), "learn", "--unit",
                               unit_one, "--type", "insight", "--text", lesson],
                              cwd=ws, env=bmember(one))
                released = fire_gate(one)
                _, recalled = run(["base", "recall", "--keyword",
                                   "acceptance run"], cwd=ws, env=benv)
                readable = "one piece" in recalled
                b.add(PASS if (released == 0 and readable) else FAIL,
                      "a Member's lesson reaches the graph and is readable back out",
                      f"gate released={released == 0}, recall found it={readable}"
                      + ("" if readable else f" :: {recalled.strip()[:160]}"))

            # 11. The guard that outranks every row above it. If this fails,
            #     the run wrote the operator's own knowledge and the PASSes are
            #     worthless.
            after_hash = operator_digest()
            b.add(PASS if before_hash == after_hash else FAIL,
                  "the operator's own graph and registry were never written to",
                  f"content hash {before_hash[:12]} unchanged"
                  if before_hash == after_hash else
                  f"OPERATOR GRAPH CHANGED {before_hash[:12]} -> {after_hash[:12]}"
                  " — this run contaminated real knowledge")
            shutil.rmtree(base_home, ignore_errors=True)

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
