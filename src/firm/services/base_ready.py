"""Is base on this machine, and does `base cadre` actually run?

Founding answered neither question. ``founding.commit`` wires the firm's domain
and writes its exports -- both of which need base -- and never once asked
whether base was there, or whether the command every Member is told to run
resolves at all. So a firm could be founded on a machine with no base, or with
the extension listed and dead, and the Board was handed something that looked
complete. That is issue #118. Chris's words for it: "firms should be fully put
together through founding, not have to be worked on to get working when they
just got founded".

TWO FACTS, NEVER ONE FLAG. ``extension_installed`` and ``extension_runs`` are
separate keys because the failure this product has already had is exactly the
state where the first is true and the second is false: ``base extension
install`` returned rc 0, printed "Installed cadre v0.2.0", listed the
extension, and left ``base cadre`` exiting 127 -- measured on base 0.15.0 and
recorded in ``base_extension``'s own module docstring. A manifest on disk and a
command that resolves are different claims, and one flag covering both hides
whichever of them is false. ``base_domain.wire_workspace`` reports its three
states separately for the same reason.

TWO KINDS OF OBSERVER, DELIBERATELY. ``extension_installed`` is a FILESYSTEM
reading: the manifest is there, it is ours, and its placeholders were filled.
``extension_runs`` is a PROCESS reading: ``base cadre --help`` exits 0. They can
disagree, and when they do, the disagreement IS the finding -- it is the F1
shape -- rather than something to average into a single verdict.

WHICH TIER THIS IS TALKING ABOUT, SAID OUT LOUD. ``_tier()`` below is the only
place in this module that decides, and today it resolves the AMBIENT tier
(``BASE_HOME`` if set, else the home directory), because that is what
``base_extension._installed_path()`` resolves and that function takes no
argument. whimbrel specified a firm-private tier at ``<firm>/.firm/base-home``
and gadwall owns building it. Until that lands, founding installs the extension
into the ambient tier; when ``base_extension.install`` grows a workspace
parameter, ``_tier()`` is the whole change.

NOTHING HERE EVER REFUSES A FOUNDING. base being absent is a fact about the
host, not a defect in Cadre: a licensee may not carry base, and a firm without
it is degraded, never broken. That is the contract ``base_domain.sync``,
``base_export.export`` and ``base_extension.install`` all keep, and this module
keeps it too. It reports what is missing, by name, and ``founding.commit`` goes
on to make the firm.

SKIPPED AND FAILED ARE DIFFERENT FACTS, and ``skipped`` is a FIELD rather than a
phrase in ``reason``. ``base_extension`` learned that the hard way in issue #83:
``run_install`` decided an exit code by looking for "not installed" inside a
human sentence, and a base living under a directory named "not installed"
carried the phrase into a genuine refusal, which was then reported as a success.
Prose is for the operator; the field is for the caller.

WHICH DIRECTORY THE PROBE STOOD IN. Every probe here passes an explicit ``cwd``
and the reading reports it as ``probe_cwd``. ``BASE_HOME`` isolates the HOME
tier only -- base also resolves a WORKSPACE tier by walking up from the current
directory -- so a probe that inherits whatever directory the hub happens to be
in is a reading whose subject nobody can name afterwards. With a workspace it
stands in the firm; without one it stands where the caller stands, and says so.
``base_extension.install`` runs its own handler check with ``cwd=Path.cwd()``
(``base_extension.py``, the ``base cadre --help`` call), which this module does
not own and does not change.

NEITHER FUNCTION HERE RAISES. ``check`` is called from a screen render and
``ensure`` from the middle of a founding; an exception in either would cost a
firm over a host problem that has nothing to do with the org being created.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from firm.core.proc import run_utf8

#: What an operator is told is missing. Plain nouns, because these reach a
#: screen: "base" is the binary, "the cadre extension for base" is the manifest
#: plus the command it installs.
MISSING_BASE = "base"
MISSING_EXTENSION = "the cadre extension for base"

_TIMEOUT_SEC = 60


def _tier(workspace: Path | str | None = None) -> tuple[Path, dict[str, Any]]:
    """The ONE place this module decides which base tier it is talking about.

    Returns the manifest path to read back, and the keyword arguments to hand
    ``base_extension.install``.

    Today the answer ignores ``workspace``: ``base_extension._installed_path()``
    takes no argument and resolves the ambient tier, so that is the tier
    founding validates and installs into. The parameter is carried anyway
    because every caller already has the firm and the answer is about to depend
    on it -- whimbrel's spec puts the manifest in ``<firm>/.firm/base-home`` and
    gadwall owns that lane. When it lands, this function passes the workspace
    through and nothing else in this module or in founding changes.
    """
    from firm.services import base_extension

    return base_extension._installed_path(), {}


def _blank() -> dict[str, Any]:
    return {
        "ok": False,
        # `skipped` means exactly what it means in `base_extension.install`:
        # this machine has no base, so the step did not happen and did not
        # fail. A caller deciding anything from the reason TEXT instead of this
        # field is repeating issue #83.
        "skipped": False,
        "base_present": False,
        "base_runs": False,
        "base_may_write_the_tier": False,
        "base_path": "",
        "extension_installed": False,
        "extension_runs": False,
        "manifest_path": "",
        "probe_cwd": "",
        "missing": [],
        "reason": "",
    }


def _probe_cwd(workspace: Path | str | None) -> str:
    """The directory every probe in this module stands in, named out loud.

    base resolves a WORKSPACE tier by walking up from the current directory,
    which `BASE_HOME` does not touch. So a probe that simply inherits whatever
    directory the hub was started in produces a reading whose subject cannot be
    named afterwards -- the shape that once had a "fresh install is UNHEALTHY"
    line reported about an operator's own workspace graph.

    With a workspace, stand in the firm. Without one, stand where the caller
    stands and report it, so the answer is at least attributable.
    """
    if workspace is not None:
        candidate = Path(workspace)
        if candidate.is_dir():
            return str(candidate)
    try:
        return str(Path.cwd())
    except OSError:
        return ""


def _env() -> dict[str, str]:
    """The env every `base` call gets -- base_domain's builder, never a copy.

    ``base_extension`` reaches for the same one and says why: two copies of an
    environment builder is two chances to configure a subprocess wrong, and
    these two drifted once already (one learned USERPROFILE so a spawned Cadre
    could find a home directory on Windows, the other did not).
    """
    from firm.services.base_domain import _base_env

    return _base_env()


def check(workspace: Path | str | None = None) -> dict[str, Any]:
    """Read the state of base and of `base cadre`. Installs nothing, writes nothing.

    This is what a screen calls. ``readiness`` renders it on every view of the
    firm, so it must stay read-only: a check that quietly repairs is a worse
    defect than one that reports, because nobody can tell afterwards what the
    machine looked like before it was looked at.
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

        try:
            probe = run_utf8([base, "--version"], capture_output=True,
                             timeout=_TIMEOUT_SEC, env=_env(), cwd=cwd or None,
                             stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["missing"] = [MISSING_BASE]
            result["reason"] = (f"base is at {base} but it could not be run: {exc}")
            return result
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout or "").strip().splitlines()
            result["missing"] = [MISSING_BASE]
            result["reason"] = (
                f"base is at {base} but it does not run: "
                + (detail[-1][:200] if detail else f"rc={probe.returncode}"))
            return result
        result["base_runs"] = True

        manifest, _ = _tier(workspace)
        result["manifest_path"] = str(manifest)

        # #75, reused rather than re-implemented. A base built for another
        # platform still executes here and ignores BASE_HOME, so it would write
        # the operator's own tier. `install` refuses such a base before running
        # anything; reporting it here is what gives the operator the reason by
        # name instead of a repair that silently never happens.
        from firm.sysconfig.binaries import base_can_honour_tier

        may_write, refusal = base_can_honour_tier(base, manifest)
        result["base_may_write_the_tier"] = bool(may_write)

        # Detector one: the filesystem. The same three reads `install`'s own
        # read-back makes, because a manifest that landed with a placeholder
        # still in it is not an installed extension.
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

        # Detector two: the process. This is the one F1 got past -- everything
        # above was true while `base cadre` was dead on every installed copy.
        try:
            ran = run_utf8([base, "cadre", "--help"], capture_output=True,
                           timeout=_TIMEOUT_SEC, env=_env(), cwd=cwd or None,
                           stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["missing"] = [MISSING_EXTENSION]
            result["reason"] = f"`base cadre` could not be run: {exc}"
            return result
        result["extension_runs"] = ran.returncode == 0

        if result["extension_runs"] and result["extension_installed"]:
            result["ok"] = True
            result["reason"] = f"base is here and `base cadre` runs; manifest at {manifest}"
            return result

        result["missing"] = [MISSING_EXTENSION]
        if not result["extension_runs"]:
            detail = (ran.stderr or ran.stdout or "").strip().splitlines()
            said = detail[-1][:200] if detail else f"rc={ran.returncode}"
            result["reason"] = (
                f"`base cadre` does not run, so no Member can use it: {said}"
                if result["extension_installed"] else
                f"the cadre extension is not installed in {manifest}, so "
                f"`base cadre` does not run: {said}")
            if not may_write:
                result["reason"] += f" — and it cannot be installed here: {refusal}"
        else:
            # Installed reads false while the command runs: base is resolving
            # the extension from somewhere other than the tier this check is
            # pointed at. Say that rather than picking a side.
            result["reason"] = (
                f"`base cadre` runs, but no Cadre manifest was read back from "
                f"{manifest} — base is resolving the command from another tier")
        return result
    except Exception as exc:                      # never raise: see module docstring
        result["reason"] = f"the base check did not complete: {exc}"
        return result


def ensure(workspace: Path | str | None = None) -> dict[str, Any]:
    """`check`, and where the extension is missing or dead, install it once.

    This is the half that answers Chris: "then run the commands to have the
    firm set up ready to use through the founding aspect". A founding that
    detects a missing extension and leaves the operator a command to type has
    not removed the manual step, it has documented it.

    The repair is ``base_extension.install``, which validates the rendered
    manifest before installing it, reads the landed file back, runs the handler,
    and never raises. It is called AT MOST ONCE. Afterwards this function calls
    ``check`` again rather than believing the install's own verdict: an install
    reporting success over a dead command is the exact defect this lane exists
    for, so the state that gets reported is one read after the writer had
    finished, not the writer's opinion of its own work.

    Adds to ``check``'s keys:

    ``install``    the whole result dict ``base_extension.install`` returned,
                   or ``{}`` when no repair was attempted.
    ``repaired``   True only when the machine was not ready before and is now.
    ``skipped``    carried from ``install``'s own FIELD of that name, never
                   inferred from its reason text or an exit code. A machine
                   with no base did not fail to install anything; it was never
                   asked to. Issue #83 is what reading the prose instead cost.
    """
    before = check(workspace)
    before["install"] = {}
    before["repaired"] = False

    # Nothing to install into. Degraded, named, and no subprocess is spawned.
    if not before["base_present"] or not before["base_runs"]:
        return before
    if before["ok"]:
        return before

    try:
        from firm.services import base_extension

        _, install_kwargs = _tier(workspace)
        installed = base_extension.install(**install_kwargs)
    except Exception as exc:                      # never raise: see module docstring
        before["reason"] = f"the cadre extension could not be installed: {exc}"
        return before

    after = check(workspace)
    after["install"] = installed
    after["repaired"] = bool(after["ok"])
    # The FIELD, never the sentence. base disappearing between the check and
    # the install is the only way to reach this, and it is a host fact rather
    # than a failed repair.
    after["skipped"] = bool(after["skipped"] or installed.get("skipped"))

    if not after["ok"]:
        reason = str(installed.get("reason") or "").strip()
        if installed.get("ok") and not after["ok"]:
            # The two observers disagree: the installer proved the handler ran,
            # a fresh probe here says it does not. Report the disagreement
            # rather than preferring whichever one is more convenient.
            after["reason"] = (
                "the installer reported the cadre extension is installed and "
                "running, but a second probe here disagrees: " + after["reason"])
        elif reason:
            after["reason"] = reason
    return after
