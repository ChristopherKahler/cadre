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
import re
import sys
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from firm.core.proc import run_utf8

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


def _base_env(workspace: Path | str | None = None) -> dict[str, str]:
    """The env every `base` call gets. ONE definition, deliberately.

    This was a second copy of base_domain's helper. They drifted the moment
    one of them was fixed: base_domain's learned to carry USERPROFILE so a
    spawned Cadre could find a home directory on Windows, and this one did
    not, so `cadre extension install` kept dying with

        RuntimeError: Could not determine home directory.

    while every other base call had been repaired. Two copies of an
    environment builder is two chances to configure a subprocess wrong.

    THE WORKSPACE IS FORWARDED, NEVER DROPPED (#117). With one, the shared
    builder points `BASE_HOME` at the firm's own tier, and that one key is the
    whole of where `base extension install` writes. When #117 taught the shared
    builder to take a workspace, this delegator still took none. So every base
    call in this module stayed on the operator's tier while the function the
    isolation tests name was already correct: a fix that reads as complete and
    moves nothing.
    """
    from firm.services.base_domain import _base_env as _shared

    return _shared(workspace)


def _installed_path(workspace: Path | str | None = None) -> Path:
    """Where the installed manifest is READ BACK from, and the tier a refusal names.

    With a workspace this is the FIRM's own tier (#117): a firm's extensions
    are a whitelist, and the only entry Cadre puts there is this manifest.
    Without one the old behaviour stands, which is what `cadre extension
    install` outside a firm still needs.

    THIS PATH NEVER DECIDES WHERE THE MANIFEST GOES. base writes wherever the
    `BASE_HOME` in its env points, so the destination moves with
    `_base_env(workspace)` and with nothing here. That is why both must be
    handed the SAME workspace. A read-back aimed at a different tier from the
    env is reading a file this install did not write: it reports False over a
    good install when that tier is empty, and when that tier holds an older
    Cadre manifest it reads the old one back as proof of the new one.
    """
    if workspace is not None:
        from firm.services.graph_isolation import tier_extensions_dir

        return tier_extensions_dir(workspace) / "cadre.toml"
    home = os.environ.get("BASE_HOME")
    root = Path(home) if home else Path.home()
    return root / ".base-gbl" / "extensions" / "cadre.toml"


# ── The graph copy of a prompt domain, and why this check exists ─────────────
# Issue #115. An extension's prompt domain has TWO homes, and they are not the
# same store. base MATCHES the domain's keywords from the installed manifest,
# but it SERVES the rule text from the GRAPH whenever the graph holds a copy of
# that domain — and the graph copy wins outright. Measured on base 0.15.2,
# 2026-09-14, with one generation of rules in the graph and a different
# generation in this manifest: every session received the graph's rules and no
# session received the manifest's. Not merged. Replaced.
#
# `base domain sync` is what puts a copy there. An install writes none and an
# uninstall removes none, so a machine can serve a copy written months ago from
# a manifest that no longer exists. That is what the maintainer's machine
# carries today: three rules naming one firm's WSL paths, standing in for this
# file's firm-neutral three, on a keyword this file declares.
#
# Cadre cannot clean that up, and saying otherwise would be F1's shape again.
# base 0.15.2 exposes no verb that removes such a rule, measured one at a time:
#
#   base rule remove --index N   matches a stored `index` triple. Rules written
#                                by domain sync carry none, so no N reaches
#                                them. Control: a rule added by `base rule add`
#                                into the same domain WAS removed by the same
#                                command, at the index the listing showed for
#                                a different row.
#   base domain sync             appends on a populated graph instead of
#                                replacing: three rules became six under one
#                                name, and 26 domains were rewritten.
#   base graph supersede         does not index rule records at all.
#   base graph apply-ops         retires only facts carrying a sync fact id,
#                                which these never had.
#
# So DoD 1 and 2 of issue #115 — actually removing them, and leaving exactly one
# rule set after an install — are OWNED BY BASE, not by this file. What this
# file can do, and what the code below is, is refuse to let a second rule set
# sit there SILENTLY: read back what base will really serve and say so.

RULE_LINE = re.compile(r"^ {2}(workspace|global) +(\d+)\. ?(.*)$")

_HEADER = re.compile(r"^\[(?P<name>[^\]]+)\] (?P<count>\d+) rules? across both tiers:")
_NO_RULES = "No rules for domain"


class GraphReadFailed(RuntimeError):
    """base's rule listing could not be read.

    Raised instead of returning an empty list, because a zero that means "I
    could not see" is indistinguishable from a zero that means "nothing is
    there" — and only one of those is good news. A caller must report the
    failure, never a count.
    """


def parse_rule_listing(text: str) -> list[str]:
    """The rule texts in a `base rule list --domain <d>` listing.

    Self-checking on purpose. The listing states its own total in the header,
    so the parsed rows are reconciled against that number and a mismatch raises
    rather than returning the short list. A parser that silently returns fewer
    rules than the tool reported is a blind detector that reads CLEAN.
    """
    if _NO_RULES in text:
        return []
    header = None
    for line in text.splitlines():
        header = _HEADER.match(line.strip())
        if header:
            break
    if header is None:
        raise GraphReadFailed(
            "base's rule listing has neither the "
            f"{_NO_RULES!r} sentence nor a parseable header; refusing to "
            f"report a rule count from it: {text.strip()[:300]!r}")
    rules = [m.group(3) for m in
             (RULE_LINE.match(line) for line in text.splitlines()) if m]
    declared = int(header.group("count"))
    if len(rules) != declared:
        raise GraphReadFailed(
            f"base reported {declared} rules for {header.group('name')!r} and "
            f"{len(rules)} lines parsed as rules; refusing to report either "
            "number")
    return rules


def manifest_domains(rendered: str) -> list[tuple[str, list[str]]]:
    """(full domain name, rules) for every prompt domain this manifest declares.

    The full name is what base calls the domain once it is installed:
    `ext:<extension>:<domain>`. That namespacing is the whole reason two
    generations can collide — the name is derived, so a domain cannot rename
    itself out of a collision.
    """
    parsed = tomllib.loads(rendered)
    ext = parsed.get("extension", {}).get("name", "")
    blocks = (parsed.get("hooks", {}).get("user_prompt", {}).get("domains", []))
    return [(f"ext:{ext}:{d['name']}", list(d.get("rules", []))) for d in blocks]


def graph_rules(base: str, domain: str, env: dict[str, str]) -> list[str]:
    """What base's graph will serve for this domain, from the CURRENT directory.

    cwd is load-bearing and is the caller's to control. base resolves the
    workspace tier by walking up from the working directory, so the identical
    command answers "3 rules" in one directory and "No rules for domain" one
    level down. A count reported without its directory is not a measurement.
    """
    # run_utf8, never a bare `subprocess.run(text=True)`. This is #114 and I
    # put it back in my own code before catching it: `text=True` decodes with
    # the host locale, which is cp1252 on the operator's Windows install. The
    # rule text being read here is full of what that mangles -- the shipped
    # rules carry backticks and an em dash, the stale ones carry `§`. Two
    # consequences, and the second is worse than the crash:
    #
    #   a rule carrying a character whose UTF-8 uses 0x81/0x8D/0x8F/0x90/0x9D
    #   (`═`, `←`, a ZWJ) KILLS the call outright;
    #
    #   and everything else decodes to a DIFFERENT string, so every graph rule
    #   compares unequal to the manifest rule it is a copy of, and a clean
    #   machine reports a collision on every domain. A false-positive
    #   generator on every Windows box, from a decode nobody would look at.
    #
    # require_output=True because the output IS the answer: base always prints
    # either a listing or the "No rules" sentence, so an empty stdout is a
    # failed read, and without this it would parse to [] and read as CLEAN.
    from firm.core.proc import NoOutput

    try:
        listed = run_utf8(
            [base, "rule", "list", "--domain", domain],
            capture_output=True, timeout=60, require_output=True,
            env=env, stdin=subprocess.DEVNULL)
    except NoOutput as exc:
        raise GraphReadFailed(
            f"`base rule list --domain {domain}` printed nothing, so what the "
            f"graph serves is unknown: {exc}") from exc
    if listed.returncode != 0:
        raise GraphReadFailed(
            f"`base rule list --domain {domain}` exited {listed.returncode}: "
            f"{(listed.stderr or listed.stdout).strip()[:300]}")
    return parse_rule_listing(listed.stdout)


def foreign_rules(rendered: str, base: str, env: dict[str, str]) -> list[dict[str, Any]]:
    """Rules the graph will serve that this manifest did not write.

    One entry per domain that carries any. An empty list means every domain
    this manifest declares serves this manifest's own rules — either because
    the graph holds no copy, or because the copy it holds still matches.
    """
    findings: list[dict[str, Any]] = []
    for domain, mine in manifest_domains(rendered):
        served = graph_rules(base, domain, env)
        if not served:
            continue
        unknown = [r for r in served if r not in mine]
        if unknown:
            findings.append({"domain": domain, "serving": served,
                             "foreign": unknown, "manifest": mine})
    return findings


def install(framework_dir: Path | str | None = None,
            workspace: Path | str | None = None) -> dict[str, Any]:
    """Render, validate, install, read back. Never raises.

    ``ok`` False with ``reason`` "base is not installed" is not an error — a
    licensee may not carry base, and a firm without it is degraded, never
    broken. That is the same contract ``base_domain.sync`` keeps.

    ``workspace`` names the firm whose OWN tier receives the manifest (#117).
    A firm's tier is a whitelist and this manifest is its one entry, and a
    Member spawned in that firm runs `base cadre` against that tier and no
    other, so an install anywhere else leaves every Member's command dead.
    With a workspace, all FOUR `base` calls below (validate, install, the
    handler proof, the graph read) carry that firm's `BASE_HOME`, the read-back
    reads that tier, and a refusal names it. A call left on the old env is not
    a smaller leak. It is the same leak, found later. With no workspace the
    manifest goes where it always went: the tier `BASE_HOME` names, or the
    home tier.
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
                              "skipped": False, "collision": False,
                              "graph_read": False, "foreign_rules": [],
                              "handler": "", "path": "", "reason": "", "cwd": ""}
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

    # The FIRM's tier when there is one. base_can_honour_tier decides on the
    # binary's image format alone, so this does not change WHETHER the install
    # refuses; it changes which tier the refusal tells the operator it
    # protected, and the `path` it reports. Naming the operator's tier while
    # refusing an install into a firm would send them to check the wrong place.
    expected = _installed_path(workspace)
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
        # ONE env, bound once, for every base call in this function: validate,
        # install, `base cadre --help`, and the rule listing inside
        # foreign_rules. Each of them takes `env=env`. A call that builds its
        # own with `_base_env()` runs in the operator's tier while the rest run
        # in the firm's, and nothing downstream reads differently.
        env = _base_env(workspace)

        checked = run_utf8(
            [base, "extension", "validate", str(staged)],
            capture_output=True, timeout=60,
            env=env, stdin=subprocess.DEVNULL)
        if checked.returncode != 0:
            result["reason"] = ("the manifest did not validate, so nothing was "
                                f"installed: {(checked.stderr or checked.stdout).strip()[:300]}")
            return result
        result["validated"] = True

        placed = run_utf8(
            [base, "extension", "install", str(staged)],
            capture_output=True, timeout=60,
            env=env, stdin=subprocess.DEVNULL)
        if placed.returncode != 0:
            result["reason"] = ("validated but the install failed: "
                                f"{(placed.stderr or placed.stdout).strip()[:300]}")
            return result
        result["installed"] = True

        # The same workspace the env was given. See _installed_path: a
        # read-back aimed at another tier reads a file this install never
        # wrote.
        landed = _installed_path(workspace)
        result["path"] = str(landed)
        if not landed.exists():
            result["reason"] = (f"base reported success but {landed} does not exist")
            return result
        on_disk = landed.read_text(encoding="utf-8")
        # The [extension] NAME, parsed, never a substring of the file. Cadre's
        # manifest carries `name = "cadre"` twice: once under [extension] and once
        # under [[commands]]. A substring search therefore read back ANY landed
        # manifest that declares a `cadre` command as Cadre's own, whatever
        # extension it belonged to.
        try:
            declared = tomllib.loads(on_disk).get("extension")
        except tomllib.TOMLDecodeError as exc:
            result["reason"] = f"{landed} exists but is not a readable manifest: {exc}"
            return result
        if not isinstance(declared, dict) or declared.get("name") != "cadre":
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
            ran = run_utf8(
                [base, "cadre", "--help"],
                capture_output=True, timeout=60,
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

        # THE GRAPH COPY, issue #115. Everything above proves the MANIFEST
        # landed and its handler runs. None of it says a word about what a
        # Member will actually be told, because base serves a domain's rule
        # text from the graph when the graph holds a copy — see the block
        # above `install`. So read that back too, from this directory, and
        # report it.
        #
        # This never fails the install. The manifest did land, the handler
        # does run, and refusing here would punish an operator for a defect
        # base owns and has given them no verb to fix — on the exact machine
        # the collision is blocking. The install's job is to stop this being
        # SILENT. Whether it should also REFUSE is an open ruling; if it
        # becomes one, it goes here and nowhere else.
        result["cwd"] = os.getcwd()
        try:
            found = foreign_rules(rendered, base, env)
        except GraphReadFailed as exc:
            result["graph_read"] = False
            result["reason"] = (
                f"installed and read back from {landed}, but the graph copy "
                f"could not be read, so it is UNKNOWN what will be served: {exc}")
            result["ok"] = True
            return result
        result["graph_read"] = True
        result["foreign_rules"] = found
        result["collision"] = bool(found)

        result["ok"] = True
        if found:
            names = ", ".join(f["domain"] for f in found)
            total = sum(len(f["foreign"]) for f in found)
            result["reason"] = (
                f"installed and read back from {landed}, but base's graph will "
                f"serve {total} rule(s) this manifest did not write, under "
                f"{names} — the graph copy wins, so those are what a Member "
                f"receives. Workspace tier resolved from {result['cwd']}.")
            return result
        result["reason"] = f"installed and read back from {landed}"
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"the install did not run: {exc}"
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_install(framework_dir: Path | str | None = None,
                workspace: Path | str | None = None) -> int:
    """`cadre extension install`. Installs the manifest into base, returns 0.

    ``workspace`` goes straight to ``install``, and this function never guesses
    one. The CLI resolves "the firm you are standing in" before it calls here.
    A Python caller therefore never has a firm chosen for it by whichever
    directory the interpreter happens to be running in, which matters because
    naming a workspace creates that firm's tier on disk.

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

    result = install(framework_dir, workspace=workspace)
    if result.get("ok"):
        print(result.get("reason") or "installed")
        if result.get("handler_runs"):
            print(f"handler runs: {result['handler']}")
        if result.get("graph_read") is False:
            print("WARNING: the graph copy of this extension's prompt "
                  "domain(s) could not be read, so what a Member will be told "
                  "is unknown.", file=sys.stderr)
        for finding in result.get("foreign_rules", []):
            # Loud, and every rule printed in full. A report that says only
            # "a collision exists" leaves the operator exactly where they
            # started: unable to tell which of two rule sets is reaching
            # their Members.
            print("", file=sys.stderr)
            print(f"COLLISION on domain {finding['domain']}:", file=sys.stderr)
            print(f"  base's graph will serve {len(finding['serving'])} rule(s) "
                  f"and this manifest declares {len(finding['manifest'])}. "
                  "The graph wins.", file=sys.stderr)
            print(f"  workspace tier resolved from: {result.get('cwd', '?')}",
                  file=sys.stderr)
            for rule in finding["foreign"]:
                print(f"  NOT FROM THIS MANIFEST: {rule}", file=sys.stderr)
            # NAME THE OWNER. Without this line the next reader spends a day
            # inside Cadre looking for a bug that is not here.
            print("  OWNER: base, not Cadre. base serves a domain's rules from "
                  "its graph and gives no verb that removes one written by "
                  "`base domain sync` (issue #115). Cadre installed correctly "
                  "and cannot clean this up. See them yourself with: "
                  f"base rule list --domain {finding['domain']}",
                  file=sys.stderr)
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
