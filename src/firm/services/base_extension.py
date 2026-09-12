"""Install Cadre's base extension manifest, and refuse to install a bad one.

The manifest source of truth ships in the repo at
``firm/base_ext/cadre.toml.template`` so ``pip install cadre`` carries it. Two
values in it are machine-specific — ``framework_dir`` and the command handler —
so installing is: render those placeholders, write the result to a temporary
file, ask base to validate it, and only then ask base to install it. Validate
before install, every time, with no path that skips it.

THE FILENAME IS PART OF THE PRODUCT, and this is F6. There are two ways to
install this manifest and they used to disagree. The product's own route
renders the placeholders; the obvious hand route —

    base extension install <site-packages>/firm/base_ext/cadre.toml

— did not, and measured on base 0.15.0 it returned rc 0, printed "Installed
cadre v0.2.0", listed the extension, and left ``base cadre`` exiting 127. An
install that reports success over a dead command is the same fault as F1.

Neither ``base extension validate`` nor ``base extension install`` looks at
whether a handler path exists (measured: rc 0 on a handler pointing at
nothing), so base cannot be made to refuse on the manifest's CONTENT. It does
refuse on a missing FILE: rc 1, "Cannot install: invalid manifest: Cannot read
file". So the shipped file is named ``cadre.toml.template`` and no
``cadre.toml`` exists in the package at all — the hand route now refuses at
install time, which is the whole of the F6 verdict.

The second half is for whoever types the ``.template`` path anyway. base echoes
the unresolved handler verbatim in its error, so the placeholder is written to
BE the instruction:

    base: command 'cadre' (ext:cadre) — handler not found:
      .../{{handler-UNRENDERED-run-cadre-extension-install-instead}}

Two things this module refuses to do, both because they have already gone wrong
once on this machine:

*It never installs a manifest it did not validate.* ``base extension install``
does not re-check anything the author got wrong, and a manifest can be accepted
while carrying a key that does nothing at all.

*It never claims an install it did not read back.* ``base extension install``
exiting 0 is the writer's own opinion of its own work. The installed file is
read from disk afterwards and its name and version compared, which is the only
thing that can say the manifest actually landed.
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PLACEHOLDER = "{{framework_dir}}"
# Not "{{handler}}". base prints the unresolved path back at the user when a
# command's handler is missing, so this token is written to read as the fix —
# see the module docstring. The name is load-bearing, not decoration.
HANDLER_PLACEHOLDER = "{{handler-UNRENDERED-run-cadre-extension-install-instead}}"


MANIFEST_TEMPLATE_NAME = "cadre.toml.template"


def manifest_source() -> Path:
    """The shipped manifest TEMPLATE, inside the installed package.

    ``.template``, never ``cadre.toml``. The suffix is what makes the hand
    install refuse instead of succeeding over a dead command — F6, explained in
    full in the module docstring. ``_installed_path`` and the staged temp file
    keep the plain ``cadre.toml`` name, because that is what base writes and
    what it derives the extension name from.
    """
    return Path(__file__).resolve().parent.parent / "base_ext" / MANIFEST_TEMPLATE_NAME


def framework_root() -> Path:
    """The repo or install root the handler is resolved against.

    ``bin/cadre`` sits beside ``src/``, so the root is two levels above the
    ``firm`` package in a source checkout and the package parent otherwise.
    """
    firm_pkg = Path(__file__).resolve().parent.parent
    candidate = firm_pkg.parent.parent          # <root>/src/firm -> <root>
    if (candidate / "bin" / "cadre").exists():
        return candidate
    return firm_pkg.parent


def console_script() -> Path:
    """This machine's `cadre` console script — the thing base must actually run.

    There is no single relative path that works everywhere, which is the whole
    of F1. `bin/cadre` resolved against framework_dir is correct in a source
    checkout, where framework_dir is the repo root, and wrong from an installed
    wheel, where framework_dir is site-packages and `site-packages/bin/cadre`
    exists on neither platform.

    A venv puts its console scripts beside its interpreter — `bin/` on POSIX,
    `Scripts/` on Windows, with `.exe` — so `sys.executable` is the honest
    anchor for an installed package. A source checkout is preferred when it has
    a real `bin/cadre`, because that is what a developer is actually running.
    """
    repo = framework_root() / "bin" / "cadre"
    if repo.exists():
        return repo
    here = Path(sys.executable).parent
    return here / ("cadre.exe" if os.name == "nt" else "cadre")


def render(framework_dir: Path | str | None = None) -> str:
    """The manifest with its machine-specific values filled in."""
    root = Path(framework_dir) if framework_dir else framework_root()
    text = manifest_source().read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        raise ValueError(
            f"{manifest_source()} carries no {PLACEHOLDER}; refusing to install a "
            "manifest whose framework_dir was hard-coded for another machine")
    if HANDLER_PLACEHOLDER not in text:
        raise ValueError(
            f"{manifest_source()} carries no {HANDLER_PLACEHOLDER}; refusing to "
            "install a manifest whose handler was hard-coded for another "
            "machine — that is F1, where the handler pointed at a path that "
            "exists on no platform and every check still read clean")
    return (text.replace(PLACEHOLDER, root.as_posix())
                .replace(HANDLER_PLACEHOLDER, console_script().as_posix()))


def _base_env() -> dict[str, str]:
    """The env every `base` call gets. ONE definition, deliberately.

    This was a second copy of base_domain's helper. They drifted the moment
    one of them was fixed: base_domain's learned to carry USERPROFILE so a
    spawned Cadre could find a home directory on Windows, and this one did
    not, so `cadre extension install` kept dying with

        RuntimeError: Could not determine home directory.

    while every other base call had been repaired. Two copies of an
    environment builder is two chances to configure a subprocess wrong.
    """
    from firm.services.base_domain import _base_env as _shared

    return _shared()


def _installed_path() -> Path:
    home = os.environ.get("BASE_HOME")
    root = Path(home) if home else Path.home()
    return root / ".base-gbl" / "extensions" / "cadre.toml"


def install(framework_dir: Path | str | None = None) -> dict[str, Any]:
    """Render, validate, install, read back. Never raises.

    ``ok`` False with ``reason`` "base is not installed" is not an error — a
    licensee may not carry base, and a firm without it is degraded, never
    broken. That is the same contract ``base_domain.sync`` keeps.
    """
    from firm.sysconfig.service import which_base

    # ``skipped`` is the ONE degraded-not-broken state, and it is a FIELD
    # rather than a phrase in ``reason`` on purpose. Issue #83: run_install
    # used to pick its exit code by looking for "not installed" in the reason
    # text, and these refusal sentences interpolate the resolved binary's
    # path -- so a base under a directory named "not installed" carried the
    # phrase into a genuine refusal and it was reported as a success.
    # Prose is for the operator. The field is for the caller.
    result: dict[str, Any] = {"ok": False, "validated": False, "installed": False,
                              "read_back": False, "handler_runs": False,
                              "skipped": False,
                              "handler": "", "path": "", "reason": ""}
    base = which_base()
    if not base:
        from firm.sysconfig.service import base_absence_reason

        result["skipped"] = True
        result["reason"] = (
            f"{base_absence_reason()} — the extension is skipped, not failed")
        return result

    # REFUSE BEFORE ANYTHING RUNS. Issue #75.
    #
    # The read-back at the end of this function is a DETECTOR and it stays: in
    # the 2026-09-11 incident it worked exactly as written, returned
    # read_back False and named the missing path. But by then base had already
    # written the operator's own tier. Detection after the fact is not
    # prevention, and the difference between the defect and the fix is WHEN.
    #
    # which_base() takes no parameter, so a caller cannot direct this function
    # at a binary -- which is why an isolation probe passing --root and
    # --base-bin failed to constrain it. The fix is not one more argument for a
    # callee to ignore. install() asserts its OWN resolution instead: it
    # already knows the tier it expects, so it checks that the binary it
    # resolved can honour that tier, and refuses with a reason when it cannot.
    from firm.sysconfig.binaries import base_can_honour_tier

    expected = _installed_path()
    may_run, refusal = base_can_honour_tier(base, expected)
    if not may_run:
        result["path"] = str(expected)
        result["reason"] = refusal
        return result

    try:
        rendered = render(framework_dir)
    except (OSError, ValueError) as exc:
        result["reason"] = str(exc)
        return result

    tmpdir = tempfile.mkdtemp(prefix="cadre-ext-")
    staged = Path(tmpdir) / "cadre.toml"
    try:
        staged.write_text(rendered, encoding="utf-8")
        env = _base_env()

        checked = subprocess.run(
            [base, "extension", "validate", str(staged)],
            capture_output=True, text=True, timeout=60,
            env=env, stdin=subprocess.DEVNULL)
        if checked.returncode != 0:
            result["reason"] = ("the manifest did not validate, so nothing was "
                                f"installed: {(checked.stderr or checked.stdout).strip()[:300]}")
            return result
        result["validated"] = True

        placed = subprocess.run(
            [base, "extension", "install", str(staged)],
            capture_output=True, text=True, timeout=60,
            env=env, stdin=subprocess.DEVNULL)
        if placed.returncode != 0:
            result["reason"] = ("validated but the install failed: "
                                f"{(placed.stderr or placed.stdout).strip()[:300]}")
            return result
        result["installed"] = True

        landed = _installed_path()
        result["path"] = str(landed)
        if not landed.exists():
            result["reason"] = (f"base reported success but {landed} does not exist")
            return result
        on_disk = landed.read_text(encoding="utf-8")
        if 'name = "cadre"' not in on_disk:
            result["reason"] = f"{landed} exists but is not Cadre's manifest"
            return result
        if PLACEHOLDER in on_disk:
            result["reason"] = (f"{landed} still carries {PLACEHOLDER} — the "
                                "framework directory never got filled in")
            return result
        if HANDLER_PLACEHOLDER in on_disk:
            result["reason"] = (f"{landed} still carries {HANDLER_PLACEHOLDER} — "
                                "the handler never got filled in")
            return result
        result["read_back"] = True

        # THE CHECK THAT F1 GOT PAST, and the reason it got past it.
        #
        # Everything above reads back the TOML: it landed, it is ours, its
        # placeholders are filled. All of that was TRUE while `base cadre`
        # was dead on every installed copy, because the handler pointed at
        # `site-packages/bin/cadre`, which exists on no platform. This
        # function's own docstring promised it never claims an install it did
        # not read back, and it was reading back the wrong noun.
        #
        # So: run the command. `base cadre --help` is cheap, has no side
        # effects, and fails exactly when a Member would fail. A manifest that
        # installs and cannot run is worse than one that refuses to install,
        # because the refusal is visible and this was not.
        handler = console_script()
        result["handler"] = str(handler)
        if not handler.exists():
            result["reason"] = (
                f"the manifest installed but its handler {handler} does not "
                "exist, so every `base cadre <verb>` would fail")
            return result
        try:
            ran = subprocess.run(
                [base, "cadre", "--help"],
                capture_output=True, text=True, timeout=60,
                cwd=str(Path.cwd()), env=env, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"the handler could not be run: {exc}"
            return result
        if ran.returncode != 0:
            # The LAST line, never the first. The first line of a Python
            # traceback is always "Traceback (most recent call last):", which
            # names nothing — reporting it hid a real RuntimeError behind a
            # constant string on this check's very first live run.
            detail = (ran.stderr or ran.stdout or "").strip().splitlines()
            result["reason"] = (
                "the manifest installed and read back, but `base cadre` does "
                "not run: " + (detail[-1][:200] if detail else f"rc={ran.returncode}"))
            return result
        result["handler_runs"] = True
        result["ok"] = True
        result["reason"] = f"installed and read back from {landed}"
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"the install did not run: {exc}"
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_install(framework_dir: Path | str | None = None) -> int:
    """`cadre extension install`. Installs the manifest into base, returns 0.

    Until this existed, the only way to wire a firm's base extension was

        python -c "from firm.services import base_extension; base_extension.install()"

    which is the shape of step that gets skipped. A journey step an operator
    cannot perform with a command is a journey step most operators will not
    perform, and this one is the whole point of the extension: without the
    manifest in base, `base cadre brief`, `base cadre learn` and `base cadre
    complete` do not resolve at all.

    Exit codes carry the same three-way answer `install` already returns, and
    the middle one is the one worth keeping:

        0  installed and read back, or base is not on this machine
        1  base is here and refused

    base being absent is NOT a failure. A licensee may not carry base, and a
    firm without it is degraded, never broken — the same contract
    `base_domain.sync` keeps. Exiting 1 there would make a host setup fact
    read as a Cadre defect, which is exactly the shape of every instrument
    failure this repo has been fixing all week.
    """
    import sys

    result = install(framework_dir)
    if result.get("ok"):
        print(result.get("reason") or "installed")
        if result.get("handler_runs"):
            print(f"handler runs: {result['handler']}")
        return 0
    reason = result.get("reason", "unknown")
    if result.get("skipped"):
        # Absent, not broken. Say so on stdout and leave rc 0.
        #
        # This read `if "not installed" in reason` until issue #83. That
        # decided an EXIT CODE by substring-matching a human-readable
        # sentence, and install()'s refusal sentences interpolate the
        # resolved binary's path. A base under a directory named
        # "not installed" therefore carried the phrase into a refusal, and a
        # genuine refusal printed "skipped:" and returned 0.
        #
        # What made it a reporting defect rather than a contamination path:
        # the subprocess count in that hole was still ZERO, so the refusal
        # did happen and the tier was protected. Only the exit code lied.
        print(f"skipped: {reason}")
        return 0
    print(f"Error: {reason}", file=sys.stderr)
    return 1
