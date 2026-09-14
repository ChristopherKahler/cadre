"""Is base on this machine, and does `base cadre` run FOR THIS FIRM'S MEMBERS?

Founding answered neither question. ``founding.commit`` wires the firm's domain
and writes its exports -- both of which need base -- and never once asked
whether base was there, or whether the command every Member is told to run
resolves at all. So a firm could be founded with the extension dead, and the
Board was handed something that looked complete. That is issue #118. Chris's
words for it: "firms should be fully put together through founding, not have
to be worked on to get working when they just got founded".

THE TIER THAT GETS CHECKED IS THE TIER MEMBERS GET. Since #117 every Member run
is spawned with ``BASE_HOME`` pointing at the firm's own tier
(``graph_isolation.ensure_tier``), and base takes its extensions from
``BASE_HOME``. Measured 2026-09-14 on the operator's Windows base, three runs
from one directory with the same manifest bytes, only ``BASE_HOME`` differing:
the operator's tier with the manifest installed -> rc 0; a firm tier with an
EMPTY extensions directory, while the operator's tier still had the install ->
rc 127, ``base: unknown command 'cadre'``; the same firm tier holding the
identical manifest bytes -> rc 0. So an install anywhere but the firm's own tier
changes nothing a Member can see, and a check pointed anywhere else reports a
command as working while every Member gets 127. This module's first version did
exactly that, and it is the failure below one tier over.

TWO FACTS, NEVER ONE FLAG. ``extension_installed`` and ``extension_runs`` are
separate keys because the failure this product has already had is exactly the
state where the first is true and the second is false: ``base extension
install`` returned rc 0, printed "Installed cadre v0.2.0", listed the
extension, and left ``base cadre`` exiting 127 (base 0.15.0, recorded in
``base_extension``'s module docstring). One flag covering both hides whichever
of them is false. ``extension_installed`` is a FILESYSTEM reading of the firm's
tier; ``extension_runs`` is a PROCESS reading, ``base cadre --help`` run with
the firm's ``BASE_HOME``. When they disagree, the disagreement is the finding.

NO REPAIR, AND WHY. Installing into the firm's tier needs
``base_extension.install`` to take a workspace, and it does not yet. The only
install that exists writes the operator's own tier, which fixes nothing for a
Member (measured above) and puts Cadre's manifest into a tier this product has
just finished cleaning Cadre's writes out of. So ``ensure`` reports the gap by
name and installs nothing. When the firm-tier install exists, ``ensure`` is
where it goes.

NOTHING HERE EVER REFUSES A FOUNDING. base being absent is a fact about the
host, not a defect in Cadre: a firm without it is degraded, never broken -- the
contract ``base_domain.sync``, ``base_export.export`` and
``base_extension.install`` all keep.

NOTHING HERE WRITES. ``check`` is rendered on every view of the readiness
screen. The env builder this module shares with every other `base` call creates
the firm's tier when handed a workspace, so a firm whose tier does not exist yet
is REPORTED as having none rather than having one made for it by a screen.

REFUSE BEFORE ANYTHING RUNS, ``--version`` INCLUDED. A base built for another
platform still executes here -- in WSL ``which_base`` resolves the Windows base
across /mnt/c -- and it ignores a POSIX ``BASE_HOME``, so whatever it printed
would be a reading of the operator's own tier, not the firm's.
``base_extension.install`` and ``base_domain.scaffold_tier`` refuse such a base
before their first subprocess; ``check`` asks the same gate before its first.
The second version of this module asked the gate only after ``--version`` had
run, then ran ``base cadre --help`` whatever the gate said, and a founding
reported the command running for a binary its own reason said had been
"Refused before anything ran" (PR 127, G2 finding F1).

SKIPPED AND FAILED ARE DIFFERENT FACTS, and ``skipped`` is a FIELD. Issue #83:
``run_install`` decided an exit code by finding "not installed" in a human
sentence, and a base under a directory of that name turned a genuine refusal
into a reported success. Prose is for the operator; the field is for the caller.

WHICH DIRECTORY THE PROBE STOOD IN. Every probe passes an explicit ``cwd`` and
reports it as ``probe_cwd``. ``BASE_HOME`` isolates the global tier; base also
resolves a WORKSPACE tier by walking up from the current directory, so a probe
that inherits the hub's directory is a reading nobody can attribute afterwards.

NEITHER FUNCTION RAISES. An exception from a screen render or from the middle
of a founding would cost a firm over a host problem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from firm.core.proc import run_utf8

#: What an operator is told is missing. Plain nouns, because these reach a screen.
MISSING_BASE = "base"
MISSING_EXTENSION = "the cadre extension for base"

_TIMEOUT_SEC = 60


def _tier(workspace: Path | str | None) -> Path | None:
    """The ONE place this module decides which base tier it is asking about.

    The firm's own extensions directory, because that is the tier every Member
    run is spawned into (#117, ``pulse/spawn.py``). ``None`` without a
    workspace: before a firm exists there is no Member tier to ask about, and
    answering from the operator's tier instead is the exact mistake the module
    docstring records.
    """
    if workspace is None:
        return None
    from firm.services.graph_isolation import tier_extensions_dir

    return tier_extensions_dir(workspace)


def _blank() -> dict[str, Any]:
    return {
        "ok": False,
        # Means exactly what it means in `base_extension.install`: no base on
        # this machine, so nothing happened and nothing failed.
        "skipped": False,
        "base_present": False,
        "base_runs": False,
        # The #75 gate's answer for the resolved binary, asked before any
        # subprocess, with or without a firm. False also when never asked.
        "base_may_write_the_tier": False,
        "base_path": "",
        "extension_installed": False,
        "extension_runs": False,
        "extensions_dir": "",
        "manifest_path": "",
        "probe_cwd": "",
        "missing": [],
        "reason": "",
    }


def _probe_cwd(workspace: Path | str | None) -> str:
    """The directory every probe stands in, named out loud (see module docstring)."""
    if workspace is not None:
        candidate = Path(workspace)
        if candidate.is_dir():
            return str(candidate)
    try:
        return str(Path.cwd())
    except OSError:
        return ""


def _env(workspace: Path | str | None) -> dict[str, str]:
    """base_domain's env builder, never a copy -- two copies drift.

    With a workspace it points ``BASE_HOME`` at the firm's tier, which is what
    makes the process reading below a reading about Members. Callers only pass a
    workspace once the firm's extensions directory is known to exist, because
    the builder creates the tier when it is missing and this module writes
    nothing.
    """
    from firm.services.base_domain import _base_env

    return _base_env(workspace)


def _names_the_failure(proc: Any) -> str:
    """The line of a probe's output that NAMES the failure, not the advice under it.

    Measured 2026-09-14 on base 0.15.2, `base cadre --help`, rc 127, all on
    stderr. An empty extensions directory prints ``base: unknown command
    'cadre'`` and then two lines indented by two spaces ("No plugin commands
    installed. ..." and "Run `base --help` for core commands."). A manifest whose
    handler is missing prints ``base: command 'cadre' (ext:cadre) — handler not
    found: <path>`` and one indented line ("Check the extension's [[commands]]
    handler path."). The failure is the unindented line and the advice is
    indented, and quoting the LAST line quoted the advice (PR 127 G2, F6).

    The LAST unindented line rather than the first: a handler that dies in
    Python ends on its exception line, below an unindented "Traceback (most
    recent call last):" (CPython 3.12, measured the same day).
    """
    text = proc.stderr or proc.stdout or ""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f"rc={proc.returncode}"
    flush = [line for line in lines if not line[:1].isspace()]
    return (flush[-1] if flush else lines[-1]).strip()[:200]


def check(workspace: Path | str | None = None) -> dict[str, Any]:
    """Read base, and read `base cadre` in the firm's own tier. Writes nothing.

    Without a workspace only the base questions are answered: there is no firm,
    so there is no Member tier, and the extension keys stay False with a reason
    that says so rather than a reading taken from the wrong tier.
    """
    result = _blank()
    try:
        from firm.sysconfig.service import base_absence_reason, which_base

        cwd = _probe_cwd(workspace)
        result["probe_cwd"] = cwd
        base = which_base()
        if not base:
            result["skipped"] = True
            result["missing"] = [MISSING_BASE]
            result["reason"] = base_absence_reason()
            return result
        result["base_present"] = True
        result["base_path"] = str(base)

        extensions = _tier(workspace)
        manifest = extensions / "cadre.toml" if extensions is not None else None

        # REFUSE BEFORE ANYTHING RUNS -- see the module docstring. The gate reads
        # the binary's image format and runs nothing; the tier it is handed only
        # names, in the refusal, what this base would have written over.
        from firm.sysconfig.binaries import base_can_honour_tier

        may_run, refusal = base_can_honour_tier(
            base, manifest if manifest is not None else "the firm's own tier")
        result["base_may_write_the_tier"] = bool(may_run)
        if not may_run:
            result["missing"] = [MISSING_BASE]
            result["reason"] = refusal
            return result

        try:
            probe = run_utf8([base, "--version"], capture_output=True,
                             timeout=_TIMEOUT_SEC, env=_env(None),
                             cwd=cwd or None, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["missing"] = [MISSING_BASE]
            result["reason"] = f"base is at {base} but it could not be run: {exc}"
            return result
        if probe.returncode != 0:
            result["missing"] = [MISSING_BASE]
            result["reason"] = (f"base is at {base} but it does not run: "
                                + _names_the_failure(probe))
            return result
        result["base_runs"] = True

        if extensions is None or manifest is None:
            result["reason"] = (
                "base is here and runs. The cadre extension is checked in the "
                "firm's own tier, and there is no firm yet")
            return result
        result["extensions_dir"] = str(extensions)
        result["manifest_path"] = str(manifest)

        if not extensions.is_dir():
            # Report, never create. A tier made by a screen render is a write,
            # and a firm with no tier is a fact worth seeing as it is.
            result["missing"] = [MISSING_EXTENSION]
            result["reason"] = (
                f"the firm has no base tier at {extensions}, so no Member can "
                "run `base cadre`")
            return result

        # Detector one: the filesystem of the firm's tier. The same three reads
        # `base_extension.install`'s read-back makes, because a manifest still
        # carrying a placeholder is not an installed extension.
        from firm.services.base_extension import HANDLER_PLACEHOLDER, PLACEHOLDER

        landed = ""
        try:
            if manifest.exists():
                landed = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            result["reason"] = f"{manifest} could not be read: {exc}"
        if (landed and 'name = "cadre"' in landed
                and PLACEHOLDER not in landed
                and HANDLER_PLACEHOLDER not in landed):
            result["extension_installed"] = True

        # Detector two: the process, run with the firm's BASE_HOME -- the env a
        # Member actually gets.
        try:
            ran = run_utf8([base, "cadre", "--help"], capture_output=True,
                           timeout=_TIMEOUT_SEC, env=_env(workspace),
                           cwd=cwd or None, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["missing"] = [MISSING_EXTENSION]
            result["reason"] = f"`base cadre` could not be run in the firm's tier: {exc}"
            return result
        result["extension_runs"] = ran.returncode == 0

        if result["extension_runs"] and result["extension_installed"]:
            result["ok"] = True
            result["reason"] = f"`base cadre` runs in the firm's own tier ({manifest})"
            return result

        result["missing"] = [MISSING_EXTENSION]
        said = _names_the_failure(ran)
        if not result["extension_runs"] and result["extension_installed"]:
            result["reason"] = (
                f"the cadre extension is in the firm's tier but `base cadre` does "
                f"not run there, so no Member can use it: {said}")
        elif not result["extension_runs"]:
            result["reason"] = (
                f"the cadre extension is not installed in the firm's own tier "
                f"({extensions}), so every Member gets `base cadre` failing: {said}")
        else:
            result["reason"] = (
                f"`base cadre` runs with the firm's BASE_HOME but no Cadre manifest "
                f"was read back from {manifest}, so base is resolving it from "
                "somewhere this check cannot see")
        return result
    except Exception as exc:  # noqa: BLE001 - never raise from a screen or a founding
        result["reason"] = f"the base check did not complete: {exc}"
        return result


def ensure(workspace: Path | str | None = None) -> dict[str, Any]:
    """`check`, and report what founding cannot yet fix. Installs nothing.

    This is where founding puts the extension into the firm's tier once
    ``base_extension.install`` can take a workspace. Until then there is no
    install that helps a Member: the one that exists writes the operator's
    tier, which a Member never reads (measured, see the module docstring). A
    repair that fixes nothing and writes somewhere it should not is not a
    repair, so this reports the gap by name and stops.

    Adds to ``check``'s keys:

    ``install``    always ``{}`` -- no install is attempted.
    ``repaired``   always False.
    ``repair``     why nothing was installed, in words an operator can act on,
                   or "" when nothing needed installing.
    """
    state = check(workspace)
    state["install"] = {}
    state["repaired"] = False
    state["repair"] = ""
    if state["ok"] or state["skipped"] or not state["base_runs"] or workspace is None:
        return state
    state["repair"] = (
        "founding cannot install the cadre extension into the firm's own tier "
        "yet: that install is not built. Installing it into the operator's tier "
        "instead would fix nothing for any Member, who runs with the firm's tier, "
        "and would write the operator's own tier")
    return state
