"""``cadre install <wheel>`` -- install a wheel and PROVE the bytes landed.

The failure this exists for: ``pip install`` of a freshly built wheel into an
environment that already has the package printed "Requirement already
satisfied" and installed nothing, while ``--version`` reported the same frozen
literal before and after. A fix could be declared landed while the old code was
still running, and every measurement taken afterwards was taken against the
wrong bytes. Issue #120.

The version now moves with the commit, which is the structural fix and stops
pip short-circuiting at all. This command is the loud failure underneath it,
for the cases the version cannot cover: a corrupt wheel, a read-only
environment, an interpreter that is not the one you think it is.

What it does NOT do, deliberately: resolve requirements, read an index, choose
between candidates, or upgrade anything. It takes a path to a wheel and it
installs that wheel. It is not a package manager and it must not grow into one.

The shape of the check is the part worth keeping (law 22): it asserts the
installed identity CHANGED to the wheel's, read from a FRESH subprocess. It
never asserts that a file exists -- "the file is there" was true before the
install too, which is exactly how a silent no-op passes a test.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from firm.core.proc import run_utf8

# No console window, ever (Chris, 2026-09-14). A console child spawned from a
# windowless parent -- a scheduled task, a hook, pythonw -- opens a visible
# window on Windows unless told not to. getattr so this is 0 off Windows,
# where subprocess refuses any non-zero creationflags.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_COMMIT_RE = re.compile(r"^COMMIT\s*=\s*['\"]([0-9a-f]{40})['\"]", re.M)
_VERSION_RE = re.compile(r"^Version:\s*(.+)$", re.M)


def _wheel_identity(wheel: Path) -> dict[str, Any]:
    """What the wheel says it is, read out of the archive itself.

    Read from the wheel rather than from its filename: a filename is a label
    someone can rename, and the whole point of this command is that the thing
    installed is the thing meant.
    """
    out: dict[str, Any] = {"commit": None, "version": None, "readable": False}
    try:
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
            out["readable"] = True
            for name in names:
                if name.endswith("firm/_build_stamp.py"):
                    text = zf.read(name).decode("utf-8", "replace")
                    found = _COMMIT_RE.search(text)
                    if found:
                        out["commit"] = found.group(1)
                elif name.endswith(".dist-info/METADATA"):
                    text = zf.read(name).decode("utf-8", "replace")
                    found = _VERSION_RE.search(text)
                    if found:
                        out["version"] = found.group(1).strip()
    except (zipfile.BadZipFile, OSError):
        # Not a readable wheel. Reported as unreadable rather than as empty --
        # a wheel with no commit and a wheel that could not be opened are
        # different facts and must not collapse into one.
        return out
    return out


def _installed_identity(python_bin: str) -> dict[str, Any] | None:
    """Ask the target interpreter, in a FRESH process, what it now has.

    A fresh process matters. This one has already imported ``firm``; asking it
    again would answer out of its own module cache and would happily report the
    old identity after a successful install, or the new one after a failed
    install, depending on nothing but import order.
    """
    probe = (
        "import json,sys\n"
        "try:\n"
        "    from firm.identity import installed_identity\n"
        "    print(json.dumps(installed_identity()))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'error': str(exc)}))\n"
    )
    try:
        # run_utf8, not subprocess.run(text=True): the locale codec on a cp1252
        # Windows kills the reader thread on the first non-cp1252 byte and run()
        # then returns rc 0 with stdout None, which this function would read as
        # "nothing is installed". Issue #114.
        run = run_utf8([python_bin, "-c", probe], capture_output=True,
                       timeout=120, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if run.returncode != 0 or not run.stdout.strip():
        return None
    try:
        parsed = json.loads(run.stdout.strip().splitlines()[-1])
    except ValueError:
        return None
    return None if "error" in parsed else parsed


def _short(identity: dict[str, Any] | None) -> str:
    if not identity:
        return "nothing installed"
    commit = (identity.get("sources", {}).get("build", {}) or {}).get("commit")
    version = identity.get("version", "?")
    return f"{version} ({commit[:7]})" if commit else f"{version} (no commit)"


def _recorded_sha256(identity: dict[str, Any] | None) -> str | None:
    """The sha256 pip recorded for the installed wheel, or None when none was."""
    wheel = ((identity or {}).get("sources") or {}).get("wheel") or {}
    return wheel.get("sha256") or None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_install(wheel: Path, *, python_bin: str | None = None,
                as_json: bool = False) -> int:
    python_bin = python_bin or sys.executable
    result: dict[str, Any] = {"ok": False, "wheel": str(wheel),
                              "python": python_bin}

    def emit(code: int) -> int:
        if as_json:
            print(json.dumps(result, indent=2, default=str))
        return code

    if not wheel.exists():
        result["reason"] = "wheel-not-found"
        if not as_json:
            print(f"cadre install: no such file: {wheel}", file=sys.stderr)
        return emit(2)
    if wheel.suffix != ".whl":
        result["reason"] = "not-a-wheel"
        if not as_json:
            print(f"cadre install: not a wheel: {wheel}\n"
                  f"  This command takes a path to a .whl and nothing else.",
                  file=sys.stderr)
        return emit(2)

    wanted = _wheel_identity(wheel)
    result["wheel_identity"] = wanted
    if not wanted["readable"]:
        result["reason"] = "wheel-unreadable"
        if not as_json:
            print(f"cadre install: cannot read {wheel} as a wheel — it is not a "
                  f"valid zip archive. Nothing was installed.", file=sys.stderr)
        return emit(2)

    before = _installed_identity(python_bin)
    result["before"] = _short(before)

    run = run_utf8(
        [python_bin, "-m", "pip", "install", "--force-reinstall", "--no-deps",
         str(wheel)],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    result["pip_returncode"] = run.returncode

    if run.returncode != 0:
        # pip failed. Say so, show why, and leave a non-zero status. The one
        # thing that must never happen here is a zero.
        after = _installed_identity(python_bin)
        result["after"] = _short(after)
        result["reason"] = "pip-failed"
        if not as_json:
            print(f"cadre install: FAILED — pip exited {run.returncode} and "
                  f"nothing was installed.", file=sys.stderr)
            tail = (run.stderr or run.stdout or "").strip().splitlines()[-8:]
            for line in tail:
                print(f"  {line}", file=sys.stderr)
            print(f"  the environment still has: {_short(after)}",
                  file=sys.stderr)
        return emit(1)

    after = _installed_identity(python_bin)
    result["after"] = _short(after)

    if after is None:
        result["reason"] = "not-importable-after-install"
        if not as_json:
            print("cadre install: FAILED — pip reported success but the "
                  "package cannot be imported afterwards.", file=sys.stderr)
        return emit(1)

    got_commit = (after.get("sources", {}).get("build", {}) or {}).get("commit")
    got_version = after.get("version")

    # THE ASSERTION. Not "is a file there" -- the file was there before. The
    # installed identity must now BE the wheel's identity.
    mismatches = []
    if wanted["commit"] and got_commit != wanted["commit"]:
        mismatches.append(f"commit: wheel says {wanted['commit']}, "
                          f"the environment reports {got_commit}")
    if wanted["version"] and got_version != wanted["version"]:
        mismatches.append(f"version: wheel says {wanted['version']}, "
                          f"the environment reports {got_version}")
    file_sha = _file_sha256(wheel)
    result["wheel_sha256"] = file_sha
    after_sha = _recorded_sha256(after)
    if after_sha and after_sha != file_sha:
        mismatches.append(f"sha256: the wheel file is {file_sha}, "
                          f"the environment recorded {after_sha}")

    if mismatches:
        result["reason"] = "identity-did-not-take"
        result["mismatches"] = mismatches
        if not as_json:
            print("cadre install: FAILED — pip reported success but the "
                  "environment is not what the wheel says it should be.",
                  file=sys.stderr)
            for line in mismatches:
                print(f"  {line}", file=sys.stderr)
        return emit(1)

    result["ok"] = True
    before_sha = _recorded_sha256(before)
    result["sha256_before"] = before_sha
    result["sha256_after"] = after_sha
    # Bytes are compared only when both sides were read. A label is a version
    # and a short commit; two dirty builds of one commit share it and differ in
    # every byte that matters. Claiming identical bytes from equal labels is #120
    # turned around (G2 F2).
    same_bytes = (before_sha == after_sha) if (before_sha and after_sha) else None
    label_changed = result["before"] != result["after"]
    result["bytes_verified"] = same_bytes is not None
    result["changed"] = (True if (label_changed or same_bytes is False)
                         else (False if same_bytes else None))
    if not as_json:
        print(f"installed {wheel.name}")
        if label_changed:
            print(f"  {result['before']}  ->  {result['after']}")
            if before_sha and after_sha:
                print(f"  sha256 {before_sha}  ->  {after_sha}")
        elif same_bytes is False:
            print(f"  same label {result['after']}, DIFFERENT bytes:")
            print(f"  sha256 {before_sha}  ->  {after_sha}")
        elif same_bytes:
            # Not a failure, but it must be said out loud. Silence here is the
            # whole bug: an operator who reinstalls and sees nothing assumes
            # something happened.
            print(f"  identity UNCHANGED: {result['after']}")
            print(f"  the environment already held exactly these bytes (sha256 {after_sha}).")
        else:
            print(f"  label unchanged: {result['after']}")
            print("  bytes not verified: the previous install recorded no wheel hash, "
                  "so this cannot say whether the bytes changed.")
    return emit(0)
