"""#166: a Member runs the `firm` of the install that launched its pulse.

The walk on main 3acf695e founded a firm with `cadre init`, pulsed it by hand,
and the Member did the work and then could not register it: "the `firm` command
is not available in the system PATH". Nothing put the folder holding the
install's `firm.exe` on the Member's PATH (`pulse_path`, environment.py:42-77).

THE LOCK (osprey, G0 verdict, lane doc 2026-09-22-dunlin-cadre-issue-166):
  (1) a Member's `firm` and `cadre` are THIS install's, ahead of any other on
      its PATH, a stale copy in `ws/.firm/bin` included;
  (2) `python`, `pip` and every other name resolve for the Member exactly as
      before. The install's scripts folder also holds `python.exe` and
      `pip.exe`, so it can never go on the PATH whole.

So each pulse keeps `ws/.firm/entry/<install key>/` holding ONLY copies of its
own install's `firm` and `cadre`, and that folder goes first.

WHAT EACH TIER PROVES (the words of tests/test_pulse_containment.py):
  Tier A, here, every host: OUR CODE ASKS. A fake install's scripts folder is
      answered by a faked `sysconfig`, home and PATH are fake, and the real
      `pulse_environment` seam builds the Member's PATH.
  Tier B, here, every host: THE REAL INSTALL. A real hand pulse, the real
      interpreter's scripts folder, and a stand-in Member that runs `firm` BY
      BARE NAME through a shell -- Git Bash on Windows, the lookup the real
      Member used when RUN-001 failed (G0 verdict condition 12).
  Tier C, tests/test_winsched_live.py, CI's Windows job only: the timer pulse
      through Task Scheduler's pythonw launcher, and the hub's own `_fire_pulse`.
"""

from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace

import pytest

import firm
from firm.core.db import connect
from firm.core.migrate import apply_migrations
from firm.core.repo import create
from firm.pulse.environment import pulse_environment, pulse_path

WINDOWS = os.name == "nt"

#: The folder the imported `firm` package came from, handed to every child
#: this file starts so the child imports the SAME tree: `src` in CI's suite job,
#: site-packages in the clean-install job.
PACKAGE_ROOT = str(Path(firm.__file__).resolve().parents[1])

#: Read before any leg fakes PATH.
GIT = shutil.which("git")

NEW_KEYS = ("member_entry_missing", "member_entry_note")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _file(name: str) -> str:
    """The file name a runnable `name` has on this host."""
    return name + ".exe" if WINDOWS else name


def _put(folder: Path, name: str, body: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _file(name)
    path.write_bytes(body)
    path.chmod(0o755)
    return path


def _install(folder: Path, tag: str,
             names=("firm", "cadre", "python", "pip", "pythonw", "uvicorn")) -> Path:
    """A fake install's scripts folder. Each file's bytes name the install, so
    a copy can be traced back to the install it came from."""
    for name in names:
        _put(folder, name, f"#!/bin/sh\necho {tag}-{name}\n".encode())
    return folder


def _decoy(folder: Path) -> None:
    """A stale `firm` and `cadre`, the copy Chris was told to make: an `.exe` on
    Windows (the only shape Git Bash runs by bare name) beside a `.cmd`."""
    for name in ("firm", "cadre"):
        _put(folder, name, b"#!/bin/sh\necho DECOY-166\nexit 3\n")
        if WINDOWS:
            (folder / f"{name}.cmd").write_text("@echo DECOY-166\r\n@exit /b 3\r\n",
                                                encoding="utf-8")


def _schemes(monkeypatch, *, default: Path, user: Path | None) -> None:
    """Answer `sysconfig`'s scripts folders for the default and user schemes."""
    real = sysconfig.get_path
    user_scheme = sysconfig.get_preferred_scheme("user")
    absent = default.parent / "no-user-scripts-folder"

    def fake(name, scheme=None, vars=None, expand=True):
        if name == "scripts":
            if scheme is None or scheme == sysconfig.get_default_scheme():
                return str(default)
            if scheme == user_scheme:
                return str(user or absent)
        return real(name, scheme, vars, expand)

    monkeypatch.setattr(sysconfig, "get_path", fake)


def _member_path(ws: Path) -> tuple[str, dict]:
    """The PATH a Member of this pulse inherits, and what the pulse reports."""
    with pulse_environment(ws, ws / ".firm" / "firm.db", "f166") as record:
        return os.environ["PATH"], dict(record or {})


def _entry_of(found: str | None, ws: Path) -> Path | None:
    """The key folder `found` sits in, or None when it is not under .firm/entry."""
    if not found:
        return None
    parent = Path(found).resolve().parent
    return parent if parent.parent == (ws / ".firm" / "entry").resolve() else None


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake home, a fake `base` on the inherited PATH, and a workspace."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    base_dir = tmp_path / "base-bin"
    _put(base_dir, "base", b"#!/bin/sh\nexit 0\n")
    inherited = tmp_path / "inherited"
    inherited.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([str(base_dir), str(inherited)]))
    ws = tmp_path / "ws"
    (ws / ".firm").mkdir(parents=True)
    return SimpleNamespace(home=home, base_dir=base_dir, inherited=inherited,
                           ws=ws, tmp=tmp_path)


# ---------------------------------------------------------------------------
# Tier A
# ---------------------------------------------------------------------------

def test_a1_the_members_firm_and_cadre_are_this_installs(host, monkeypatch):
    """LOCK 1: this install's `firm` and `cadre` win over a stale copy anywhere.

    Decoys sit in every folder the pulse puts ahead of the inherited PATH and in
    the inherited PATH itself; `.firm/bin` is the copy the walk's workaround
    made. The folder a tool the hub recorded sits in comes after `.firm/bin`,
    so the `.firm/bin` decoy stands in for it.
    """
    install = _install(host.tmp / "install", "THIS")
    _schemes(monkeypatch, default=install, user=None)
    for folder in (host.ws / ".firm" / "bin", host.home / ".local" / "bin",
                   host.base_dir, host.inherited):
        _decoy(folder)

    path, record = _member_path(host.ws)

    entry = None
    for name in ("firm", "cadre"):
        found = shutil.which(name, path=path)
        entry = _entry_of(found, host.ws)
        assert entry is not None, (
            f"`{name}` resolved to {found}, not to the pulse's own entry folder; "
            f"the Member's PATH was {path}")
        assert Path(found).read_bytes() == (install / Path(found).name).read_bytes(), (
            f"{found} is not a copy of this install's {name}")
    listing = sorted(p.name for p in entry.iterdir())
    assert listing == sorted([_file("cadre"), _file("firm")]), (
        f"the entry folder must hold only this install's firm and cadre: {listing}")
    assert not any(k in record for k in NEW_KEYS), record


def test_a2_every_other_name_resolves_as_before(host, monkeypatch):
    """LOCK 2: `python`, `pip` and every other name resolve exactly as before.

    Before is the PATH with no install folder holding `firm` (A3c pins that
    this is main's formula). Green on main by construction; it exists for the
    mutation that puts the whole scripts folder first (M2), graded on that.
    """
    install = _install(host.tmp / "install", "THIS")
    _install(host.inherited, "SYSTEM", names=("python", "pip", "pythonw", "uvicorn"))

    _schemes(monkeypatch, default=host.tmp / "no-firm-here", user=None)
    before, _ = _member_path(host.ws)
    _schemes(monkeypatch, default=install, user=None)
    after, _ = _member_path(host.ws)

    others = sorted({Path(n).stem for n in os.listdir(install)} - {"firm", "cadre"})
    print(f"[166 A2] compared {len(others)} names: {others}")
    assert others, "compared zero names, so this leg proved nothing (law 23)"
    moved = {n: (shutil.which(n, path=before), shutil.which(n, path=after))
             for n in others
             if shutil.which(n, path=before) != shutil.which(n, path=after)}
    assert moved == {}, f"names that now resolve elsewhere (before, after): {moved}"


@pytest.mark.parametrize("case", ["default", "user", "user-site-off", "neither"])
def test_a3_the_folder_is_the_interpreters_answer(host, monkeypatch, case):
    """D1: the default scheme's scripts folder, then the user scheme's, and the
    first of the two that holds a `firm` the OS would run by name.

    The user scheme counts only when this interpreter's user site is on: a
    venv's `firm` is never in `~/.local/bin`, and on WSL the `firm` there is
    another install's (L12, measured). USER-SITE-OFF and NEITHER are the honest
    absence: no entry folder, no segment naming one, and `member_entry_missing`
    on the pulse's report saying why.
    """
    default_names = (("firm", "cadre", "python", "pip") if case == "default"
                     else ("python", "pip"))
    default = _install(host.tmp / "default", "DEFAULT", names=default_names)
    user = (_install(host.tmp / "user", "USER")
            if case in ("user", "user-site-off") else None)
    _schemes(monkeypatch, default=default, user=user)
    monkeypatch.setattr(site, "ENABLE_USER_SITE", case != "user-site-off")

    path, record = _member_path(host.ws)
    found = shutil.which("firm", path=path)
    entry_root = (host.ws / ".firm" / "entry").resolve()

    if case in ("neither", "user-site-off"):
        assert found is None, found
        assert not any(Path(seg).resolve().parent == entry_root
                       for seg in path.split(os.pathsep) if seg), path
        assert not entry_root.exists() or not any(
            p.is_dir() for p in entry_root.iterdir()), list(entry_root.iterdir())
        assert record.get("member_entry_missing"), (
            f"a pulse with no install folder must say so: {record}")
        return
    source = default if case == "default" else user
    assert _entry_of(found, host.ws) is not None, (found, path)
    assert Path(found).read_bytes() == (source / Path(found).name).read_bytes()


def test_a4_the_folder_never_comes_from_the_interpreters_own_path(host, monkeypatch):
    """D1 and L11: `dirname(sys.executable)` is wrong for a python.org install
    (`C:\\Python312` against `C:\\Python312\\Scripts`, measured), so a pulse
    run under a pythonw.exe in a folder with no `firm` still finds its own."""
    install = _install(host.tmp / "install", "THIS")
    elsewhere = host.tmp / "interpreter-home"
    interpreter = _put(elsewhere, "pythonw", b"#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(sys, "executable", str(interpreter))
    _schemes(monkeypatch, default=install, user=None)

    path, _ = _member_path(host.ws)
    found = shutil.which("firm", path=path)
    assert _entry_of(found, host.ws) is not None, (found, path)
    assert Path(found).read_bytes() == (install / Path(found).name).read_bytes()


def test_a5_a_stale_copy_is_refreshed_and_a_refused_one_is_reported(
        host, monkeypatch):
    """A copy whose bytes differ from the install's is rewritten at the next
    pulse. When Windows refuses (a running Member holds the old copy), the old
    copy stays, the pulse still runs, and `member_entry_note` says why."""
    install = _install(host.tmp / "install", "THIS")
    _schemes(monkeypatch, default=install, user=None)
    path, _ = _member_path(host.ws)
    copy = shutil.which("firm", path=path)
    entry = _entry_of(copy, host.ws)
    assert entry is not None, f"the first pulse made no entry folder: {path}"

    Path(copy).write_bytes(b"STALE")
    _, record = _member_path(host.ws)
    assert Path(copy).read_bytes() == (install / Path(copy).name).read_bytes()
    assert not any(k in record for k in NEW_KEYS), record

    Path(copy).write_bytes(b"STALE")
    real_replace = os.replace

    def refuse(src, dst, *args, **kwargs):
        if Path(dst).resolve() == Path(copy).resolve():
            raise PermissionError(13, "in use by a running Member", str(dst))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refuse)
    path, record = _member_path(host.ws)
    assert Path(copy).read_bytes() == b"STALE"
    # Case-blind: `which` answers in PATHEXT's spelling (`firm.EXE`) on Windows.
    assert Path(copy).name.lower() in record.get("member_entry_note", "").lower(), record
    assert sorted(p.name for p in entry.iterdir()) == sorted(
        [_file("cadre"), _file("firm")]), "a refused replace left a temp file"


def test_a6_each_install_has_a_folder_of_its_own(host, monkeypatch):
    """Two installs pulsing one firm never swap each other's `firm`; and on
    Windows the same folder spelled in another case is the same install."""
    x = _install(host.tmp / "x", "X")
    y = _install(host.tmp / "y", "Y")
    _schemes(monkeypatch, default=x, user=None)
    path_x, _ = _member_path(host.ws)
    _schemes(monkeypatch, default=y, user=None)
    path_y, _ = _member_path(host.ws)

    firm_x = shutil.which("firm", path=path_x)
    firm_y = shutil.which("firm", path=path_y)
    entry_x, entry_y = _entry_of(firm_x, host.ws), _entry_of(firm_y, host.ws)
    assert entry_x and entry_y and entry_x != entry_y, (firm_x, firm_y)
    assert Path(firm_x).read_bytes() == (x / Path(firm_x).name).read_bytes()
    assert Path(firm_y).read_bytes() == (y / Path(firm_y).name).read_bytes()

    if WINDOWS:     # G0 verdict condition 11
        _schemes(monkeypatch, default=Path(str(x).upper()), user=None)
        path_c, _ = _member_path(host.ws)
        assert _entry_of(shutil.which("firm", path=path_c), host.ws) == entry_x


def test_a7_the_hubs_dispatch_names_the_folder_and_writes_nothing(
        host, monkeypatch):
    """The hub's `_fire_pulse` builds its PATH with `pulse_path`, and a timer
    pulse's Member must get the same one (tests/test_pulse_environment.py:254,
    :477). So `pulse_path` NAMES the entry folder first, and only the pulse
    writes into it."""
    install = _install(host.tmp / "install", "THIS")
    _schemes(monkeypatch, default=install, user=None)

    path = pulse_path(host.ws, "f166")
    first = Path(path.split(os.pathsep)[0]).resolve()
    assert first.parent == (host.ws / ".firm" / "entry").resolve(), path
    assert not (host.ws / ".firm" / "entry").exists(), (
        "the hub's call wrote under .firm/entry")


def test_a8_the_entry_folder_holds_only_what_the_pulse_put_there(
        host, monkeypatch):
    """G0 verdict condition 13, SAFETY. The folder is first on the pulse's own
    PATH, `resolve_claude_bin` walks that PATH when `CADRE_CLAUDE_BIN` is unset,
    and a Member can write inside the workspace. So the refresh removes every
    file in its OWN key folder other than the entry points, never touches
    another key's folder or `.firm/entry/.gitignore`, and names a file it
    cannot remove."""
    from firm.pulse.spawn import resolve_claude_bin

    install = _install(host.tmp / "install", "THIS")
    _install(host.inherited, "SYSTEM", names=("python",))
    real_claude = _put(host.inherited, "claude", b"#!/bin/sh\nexit 0\n")
    _schemes(monkeypatch, default=install, user=None)

    path, _ = _member_path(host.ws)
    entry = _entry_of(shutil.which("firm", path=path), host.ws)
    assert entry is not None, f"the first pulse made no entry folder: {path}"
    planted = [_put(entry, "python", b"#!/bin/sh\necho PLANTED\n"),
               _put(entry, "claude", b"#!/bin/sh\necho PLANTED\n")]
    other_key = entry.parent / "000000000000"
    kept = _put(other_key, "keep", b"another install's")
    gitignore = entry.parent / ".gitignore"
    gitignore_before = gitignore.read_bytes() if gitignore.exists() else None

    monkeypatch.delenv("CADRE_CLAUDE_BIN", raising=False)
    with pulse_environment(host.ws, host.ws / ".firm" / "firm.db", "f166") as record:
        inside = os.environ["PATH"]
        claude, detail = resolve_claude_bin()
    record = dict(record or {})

    assert [p.name for p in planted if p.exists()] == [], "planted files survived"
    assert claude and Path(claude).resolve() == real_claude.resolve(), (claude, detail)
    python = shutil.which("python", path=inside)
    assert python and Path(python).resolve() == (host.inherited / _file("python")).resolve(), (
        f"the Member's python moved: {python}")
    assert kept.exists(), "the refresh touched another install's folder"
    assert (gitignore.read_bytes() if gitignore.exists() else None) == gitignore_before
    assert not any(k in record for k in NEW_KEYS), record

    # A file the refresh cannot remove is named, never skipped in silence.
    stuck = _put(entry, "stuck", b"#!/bin/sh\n")
    real_unlink = os.unlink

    def refuse(target, *args, **kwargs):
        if Path(target).resolve() == stuck.resolve():
            raise PermissionError(13, "in use", str(target))
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", refuse)
    monkeypatch.setattr(os, "remove", refuse)
    _, record = _member_path(host.ws)
    assert stuck.name in record.get("member_entry_note", ""), record


def test_a9_the_entry_folder_ignores_itself(host, monkeypatch):
    """G0 verdict condition 1: `.firm/entry/.gitignore` holds `*`, so a firm
    kept under git never commits the copies."""
    install = _install(host.tmp / "install", "THIS")
    _schemes(monkeypatch, default=install, user=None)
    _member_path(host.ws)
    gitignore = host.ws / ".firm" / "entry" / ".gitignore"
    assert gitignore.is_file(), "no .firm/entry/.gitignore after a pulse"
    assert gitignore.read_text(encoding="utf-8") == "*\n"


def test_a9b_git_agrees_the_copies_are_ignored(host, monkeypatch):
    """The same, asked of git itself, with a control that git can say no."""
    if not GIT:
        pytest.skip("SKIP, LOUDLY: no git on this host, so `git check-ignore` "
                    "could not run; A9 still pins the file and its content")
    install = _install(host.tmp / "install", "THIS")
    _schemes(monkeypatch, default=install, user=None)
    subprocess.run([GIT, "init", "-q", str(host.ws)], check=True)
    path, _ = _member_path(host.ws)
    copy = shutil.which("firm", path=path)
    assert _entry_of(copy, host.ws) is not None, path
    ignored = subprocess.run([GIT, "-C", str(host.ws), "check-ignore", "-q", copy])
    control = host.ws / "notes.md"
    control.write_text("kept\n", encoding="utf-8")
    kept = subprocess.run([GIT, "-C", str(host.ws), "check-ignore", "-q", str(control)])
    assert kept.returncode == 1, "control: git ignores everything here, so it proves nothing"
    assert ignored.returncode == 0, f"git would commit {copy}"


# ---------------------------------------------------------------------------
# Tier B -- a real hand pulse on the real install
# ---------------------------------------------------------------------------

#: The stand-in Member. A FILE compiled before anything starts (#147's lesson):
#: a program that does not compile leaves the same empty record as a Member
#: that never ran. It records what the Member's own process sees, runs `firm`
#: BY BARE NAME through a shell, and answers in stream-json: one assistant line
#: and one result line, the shape of tests/test_pulse_runner.py:122-134.
_MEMBER = r'''
import json, os, shutil, subprocess, sys

rec = sys.argv[1]
bash = os.environ.get("CADRE_TEST_GIT_BASH") or ""


def sh(command):
    argv = [bash, "-c", command] if os.name == "nt" else ["/bin/sh", "-c", command]
    done = subprocess.run(argv, capture_output=True, text=True)
    return {"rc": done.returncode, "out": done.stdout.strip()[-800:],
            "err": done.stderr.strip()[-800:]}


seen = {"PATH": os.environ.get("PATH", ""), "cwd": os.getcwd(),
        "which": {n: shutil.which(n) for n in ("firm", "cadre", "python", "pip")}}
if os.name == "nt":
    seen["where"] = {}
    for name in ("firm", "python", "pip"):
        done = subprocess.run(["where", name], capture_output=True, text=True)
        lines = done.stdout.splitlines()
        seen["where"][name] = lines[0] if lines else None
    seen["gitbash_firm"] = sh('cygpath -w "$(command -v firm)"')
os.makedirs("notes", exist_ok=True)
with open(os.path.join("notes", "out.md"), "w", encoding="utf-8") as fh:
    fh.write("the work\n")
seen["version"] = sh("firm --version")
seen["register"] = sh("firm doc register --unit UNIT-001 --path notes/out.md")
with open(os.path.join(rec, "member.json"), "w", encoding="utf-8") as fh:
    json.dump(seen, fh, indent=1)
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "registered notes/out.md against UNIT-001"}]}}))
print(json.dumps({"type": "result", "usage": {
    "input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0}, "total_cost_usd": 0.0,
    "stop_reason": "end_turn", "is_error": False}))
'''


def _scratch_firm(ws: Path, firm_id: str) -> None:
    """One active Member and one Unit assigned to it (the #148 leg's shape)."""
    (ws / ".firm").mkdir(parents=True, exist_ok=True)
    conn = connect(ws / ".firm" / "firm.db")
    try:
        apply_migrations(conn)
        create(conn, "firm", {"id": firm_id, "name": f"Firm {firm_id}"})
        create(conn, "member", {"id": "MEM-001", "firm_id": firm_id,
                                "name": "Lead", "role": "worker",
                                "status": "active"})
        create(conn, "operation", {"id": "OPS-001", "firm_id": firm_id,
                                   "name": "Ops"})
        create(conn, "project", {"id": "PROJ-001", "firm_id": firm_id,
                                 "operation_id": "OPS-001", "name": "Work",
                                 "status": "in_progress",
                                 "due_date": "2099-12-31"})
        create(conn, "unit", {"id": "UNIT-001", "firm_id": firm_id,
                              "project_id": "PROJ-001", "name": "Unit 1",
                              "assignee_member_id": "MEM-001"})
        conn.commit()
    finally:
        conn.close()


def _firm_state(ws: Path) -> dict:
    conn = connect(ws / ".firm" / "firm.db")
    try:
        return {
            "runs": [r[0] for r in conn.execute(
                "SELECT status FROM member_run ORDER BY rowid")],
            "unit": conn.execute(
                "SELECT status FROM unit WHERE id = 'UNIT-001'").fetchone()[0],
            "documents": conn.execute(
                "SELECT COUNT(*) FROM document WHERE parent_entity_type = 'unit' "
                "AND parent_entity_id = 'UNIT-001'").fetchone()[0],
        }
    finally:
        conn.close()


def _stub_member(root: Path, rec: Path) -> Path:
    """The stand-in, and the launcher `CADRE_CLAUDE_BIN` names."""
    program = root / "member_stub.py"
    compile(_MEMBER, str(program), "exec")      # CONTROL 0: it compiles
    program.write_text(_MEMBER, encoding="utf-8")
    rec.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        stub = root / "stub-member.cmd"
        stub.write_text(f'@"{sys.executable}" "{program}" "{rec}"\r\n',
                        encoding="utf-8")
    else:
        stub = root / "stub-member"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{program}" "{rec}"\n',
                        encoding="utf-8")
        stub.chmod(0o755)
    return stub


def _without_firm(path: str) -> str:
    """PATH with every folder that holds a `firm` or `cadre` taken out."""
    return os.pathsep.join(
        seg for seg in path.split(os.pathsep)
        if seg and not (shutil.which("firm", path=seg) or shutil.which("cadre", path=seg)))


def _git_bash() -> str | None:
    """Git Bash, the shell Claude Code's Bash tool is on Windows. Never
    `shutil.which("bash")`: on Windows that can be WSL's launcher."""
    candidates = [r"C:\Program Files\Git\bin\bash.exe"]
    if GIT:
        candidates.append(str(Path(GIT).resolve().parents[1] / "bin" / "bash.exe"))
    return next((c for c in candidates if os.path.isfile(c)), None)


def _need_git_bash() -> str:
    bash = _git_bash()
    if bash:
        return bash
    why = ("no Git Bash on this Windows host, so the Member's bare-name lookup "
           "(condition 12) cannot run")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail(f"FAIL on CI, never a skip: {why}")
    pytest.skip(why)


def _own_version(env: dict) -> str:
    """THIS install's build, asked of the interpreter directly rather than
    through any PATH: the last word of `python -m firm --version`. The program
    name before it depends on how the CLI was started (`-m firm` prints
    `cadre`, the `firm` launcher prints `firm`), so only the build is compared."""
    done = subprocess.run([sys.executable, "-m", "firm", "--version"], env=env,
                          capture_output=True, text=True, timeout=120)
    words = done.stdout.split()
    return words[-1] if words else ""


def _ran_this_build(output: str, build: str) -> bool:
    """Did `firm --version` come from THIS install's `firm`? The decoys print
    DECOY-166, and a `firm` that did not run prints nothing."""
    words = output.split()
    return len(words) == 2 and words[0] == "firm" and words[1] == build


@pytest.fixture(scope="module")
def hand_pulse(tmp_path_factory):
    """One real hand pulse, from a PATH and a home that hold no `firm`."""
    root = tmp_path_factory.mktemp("b166")
    ws = root / "ws"
    _scratch_firm(ws, "b166")
    rec = root / "rec"
    stub = _stub_member(root, rec)
    home = root / "home"
    (home / ".local" / "bin").mkdir(parents=True)

    stripped = _without_firm(os.environ.get("PATH", ""))
    env = dict(os.environ)
    env.update({"PATH": stripped, "CADRE_CLAUDE_BIN": str(stub),
                "PYTHONPATH": PACKAGE_ROOT, "HOME": str(home),
                "USERPROFILE": str(home)})
    env.pop("FIRM_ID", None)
    if WINDOWS:
        env["CADRE_TEST_GIT_BASH"] = _need_git_bash()

    # CONTROL: nothing the pulse searches may hold a `firm` before it starts,
    # or a green here would be some other install's (VOID, never a pass).
    base = shutil.which("base", path=stripped)
    reachable = os.pathsep.join(
        [stripped, str(home / ".local" / "bin"), str(ws / ".firm" / "bin")]
        + ([str(Path(base).parent)] if base else []))
    for name in ("firm", "cadre"):
        found = shutil.which(name, path=reachable)
        if found:
            pytest.skip(f"VOID: `{name}` already resolves at {found}, so this "
                        f"host cannot show which install a Member runs")

    before = _firm_state(ws)
    done = subprocess.run(
        [sys.executable, "-m", "firm", "pulse", "--workspace", str(ws),
         "--firm-id", "b166"],
        cwd=str(ws), env=env, capture_output=True, text=True, timeout=600)
    lines = [ln for ln in done.stdout.splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    member_file = rec / "member.json"
    member = (json.loads(member_file.read_text(encoding="utf-8"))
              if member_file.exists() else None)
    return SimpleNamespace(ws=ws, env=env, stripped=stripped, before=before,
                           after=_firm_state(ws), rc=done.returncode,
                           stdout=done.stdout[-3000:], stderr=done.stderr[-3000:],
                           result=result, member=member,
                           version=_own_version(env))


def test_b1_a_hand_pulse_member_runs_this_installs_firm_and_the_unit_closes(
        hand_pulse, capsys):
    """DoD (a), (d), (h): a hand pulse's Member runs THIS install's `firm` by
    bare name, its Unit closes with nothing copied by hand, and a healthy
    result line carries neither new key."""
    h = hand_pulse
    with capsys.disabled():     # condition 6: what M4 is graded from
        print(f"\n[166 B1] sys.executable = {sys.executable}")
        print(f"[166 B1] sysconfig scripts = {sysconfig.get_path('scripts')}")
        if h.member:
            print(f"[166 B1] member which = {h.member['which']}")
            print(f"[166 B1] member where = {h.member.get('where')}")
            print(f"[166 B1] member gitbash firm = {h.member.get('gitbash_firm')}")
            print(f"[166 B1] member firm --version = {h.member['version']}")
            print(f"[166 B1] member register = {h.member['register']}")
        print(f"[166 B1] firm state before {h.before} after {h.after}")
        print(f"[166 B1] pulse rc={h.rc} result={h.result}")

    assert h.member is not None, (
        f"the stand-in Member never ran. stdout: {h.stdout}\nstderr: {h.stderr}")
    assert _ran_this_build(h.member["version"]["out"], h.version), (
        f"the `firm` that ran is not this install's: it printed "
        f"{h.member['version']}, this install's build is {h.version!r}")
    assert h.member["register"]["rc"] == 0, h.member["register"]
    assert h.after["runs"] == h.before["runs"] + ["completed"], h.after
    assert h.after["documents"] == h.before["documents"] + 1, h.after
    assert h.after["unit"] == "done", h.after
    entry = _entry_of(h.member["which"]["firm"], h.ws)
    assert entry is not None, h.member["which"]
    if WINDOWS:
        assert _entry_of(h.member["where"]["firm"], h.ws) == entry, h.member["where"]
        assert _entry_of(h.member["gitbash_firm"]["out"], h.ws) == entry, (
            h.member["gitbash_firm"])
    assert isinstance(h.result, dict), h.stdout
    assert not any(k in h.result for k in NEW_KEYS), h.result


def test_b2_the_members_python_and_pip_resolve_as_before(hand_pulse):
    """DoD (f), live: the Member's `python` and `pip` are what the PATH it
    started from resolves, and the install's scripts folder is not on it."""
    h = hand_pulse
    assert h.member is not None, "the stand-in Member never ran (see B1)"
    segments = [os.path.normcase(os.path.abspath(s))
                for s in h.member["PATH"].split(os.pathsep) if s]
    scripts = os.path.normcase(os.path.abspath(sysconfig.get_path("scripts")))
    assert scripts not in segments, (
        f"the install's whole scripts folder is on the Member's PATH: {scripts}")
    without_entry = os.pathsep.join(
        s for s in h.member["PATH"].split(os.pathsep)
        if s and _entry_of(os.path.join(s, "x"), h.ws) is None)
    for name in ("python", "pip"):
        assert h.member["which"][name] == shutil.which(name, path=without_entry), (
            name, h.member["which"][name])
