"""A firm's graph is the firm's alone: nothing outside it ever writes in.

WHAT WENT WRONG, measured on the operator's Windows machine 2026-09-14 (#117).
`C:/Users/Chris/firms/seedwin/.base/graph.nq` held 13,774 lines and **86 of them
belonged to the firm**. The other 13,688 were the operator's: 13,564 lines of
Skyrim lore from an extension he installed globally, 116 lines of his own domain
definitions, and 8 rules hanging off one of those domains. Every one of them was
written INTO the firm's own named graph, `ontology#graph/ws/seedwin`.

TWO CHANNELS, AND THE SECOND IS WHY THIS IS A WHITELIST. base logs both by name
in the firm's own `.base/changes.jsonl`:

  extension.ingest:lore   ops_bytes 2460705   <- an extension's session_start ingest
  domain.sync             124 quads           <- the GLOBAL tier's domains.toml,
                                                 copied in with NO extension involved

A deny-list aimed at the extension would have shipped, passed its own tests, and
left channel 2 carrying the operator's private trigger paths into every firm.

THE FIX IS A TIER, NOT A FILTER. base resolves its global tier from `BASE_HOME`;
everything it inherits -- extensions, domains, rules, the relay store -- comes
from there. Give the firm its own `BASE_HOME` and there is no "outside" left to
filter: a hostile extension in the operator's tier is not denied, it is
invisible. Measured with base 0.15.2: a scratch tier carrying a planted
extension took 42 triples into a workspace graph; the same run with the firm's
own tier took 0, and 0 of the operator's lore and domains.

WHAT THIS MODULE DOES NOT DO. It does not remove anything. Isolation is
forward-only, so a firm founded before the fix keeps its foreign lines until
someone runs the explicit cleanup. :func:`census` is how that firm is found and
how the invariant is checked afterwards; it reads and counts, and it never
writes.
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

#: The firm's private BASE global tier, under the directory that already holds
#: every other per-machine thing a firm owns (firm.db, vault.enc, python-path).
#: base makes `<this>/.base-gbl/` underneath it on first use.
TIER_DIRNAME = "base-home"


class Isolation(Enum):
    """What could be established about a firm's isolation. Three, not two.

    ``UNKNOWN`` is not ``UNISOLATED``. base may be absent -- a supported state
    everywhere in this codebase, because a licensee need not carry it -- and a
    firm whose isolation could not be read must never be reported as a firm
    whose isolation failed. Absent is not empty is not zero.
    """

    ISOLATED = "isolated"
    UNISOLATED = "unisolated"
    UNKNOWN = "unknown"


def _as_path(workspace: Path | str) -> Path:
    """A Path, without rebuilding one the caller already handed over.

    `Path(x)` picks its flavour from `os.name` AT CONSTRUCTION, so rebuilding a
    path that already exists is what makes the Windows branch of `_base_env`
    untestable from Linux CI: the test patches `os.name` to "nt", this module
    constructs a fresh Path, and pathlib dies with "cannot instantiate
    'WindowsPath' on your system" before reaching the assertion. Dividing an
    existing Path keeps its flavour, so the arm runs on every leg of CI instead
    of quietly covering nothing off its native platform.
    """
    return workspace if isinstance(workspace, Path) else Path(workspace)


def firm_base_home(workspace: Path | str) -> Path:
    """The firm's own BASE_HOME. One definition; everything else reads it here."""
    return _as_path(workspace) / ".firm" / TIER_DIRNAME


def tier_global_dir(workspace: Path | str) -> Path:
    """Where base keeps the firm's global tier once it has run."""
    return firm_base_home(workspace) / ".base-gbl"


def tier_extensions_dir(workspace: Path | str) -> Path:
    """The firm's extensions directory -- the whole of what may reach it.

    Cadre installs exactly one manifest here, `cadre.toml`. Anything else in
    this directory is something a human put there deliberately.
    """
    return tier_global_dir(workspace) / "extensions"


def ensure_tier(workspace: Path | str) -> Path:
    """Create the firm's tier root if it is not there. Returns the path.

    Idempotent, and deliberately NOT a "did I create it" boolean: callers care
    where the tier is, and a caller that branches on "was it new" would treat a
    re-founded firm differently from a fresh one for no reason.
    """
    root = firm_base_home(workspace)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slug(name: str) -> str:
    """base's own shape for a domain name inside a subject.

    A domain named `ext:cadre:cadre-firm` appears in the graph as
    `ontology#domain/ext-cadre-cadre-firm`, so a comparison against the raw
    name from domains.toml misses it and reports the firm's own domain as
    foreign. Measured on seedwin: 15 domain lines and 12 rule lines.
    """
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


_NAME_LINE = re.compile(r"""^\s*name\s*=\s*["']([^"']+)["']""", re.MULTILINE)
_SUBJECT_EXT = re.compile(r"ontology#ext/([^/>]+)/")
_SUBJECT_DOMAIN = re.compile(r"ontology#domain/([^>/]+)")
_SUBJECT_RULE = re.compile(r"ontology#rule/([^>/]+)")


def declared_domains(path: Path) -> set[str] | None:
    """Domain names declared in a domains.toml. ``None`` when it cannot be read.

    ``None`` and ``set()`` are different answers and the caller must be able to
    tell them apart: a missing file means the allow-list is unknown, an empty
    one means the firm genuinely declares no domains.
    """
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {_slug(m) for m in _NAME_LINE.findall(body)}


def installed_extensions(workspace: Path | str) -> set[str] | None:
    """Extension names the FIRM's own tier declares. ``None`` if absent."""
    ext_dir = tier_extensions_dir(workspace)
    if not ext_dir.is_dir():
        return None
    return {p.stem for p in ext_dir.glob("*.toml")
            if not p.name.startswith("_")}


def census(workspace: Path | str) -> dict[str, Any]:
    """Count what is in a firm's graph that the firm does not own.

    The allow-list is the firm's own configuration, never a list of known-bad
    names: an `ext/<name>/` subject is the firm's only when `<name>` is an
    extension the firm's own tier declares, and a `domain/<d>` or `rule/<d>/`
    subject is the firm's only when `<d>` is a domain the firm's own config
    declares (or is derived from one of those extensions, which is how
    `ext-cadre-cadre-firm` arrives). Everything else a session wrote while the
    firm was the workspace -- notes, decisions, projects, documents -- is the
    firm's own work and is left alone.

    THE RETURN CARRIES `visited`, AND A VISITED COUNT OF ZERO OVER A FILE THAT
    EXISTS IS A FAILURE, NEVER A CLEAN FIRM. That is this whole lane's bug one
    level up: a reader that cannot see reports the same thing as a firm with
    nothing wrong. `reason` is set whenever the census could not be taken, and
    `foreign` is then left empty rather than reported as zero.
    """
    workspace = _as_path(workspace)
    graph = workspace / ".base" / "graph.nq"
    out: dict[str, Any] = {
        "visited": 0, "firm_lines": 0, "foreign_lines": 0,
        "foreign": {}, "allowed_extensions": [], "allowed_domains": [],
        "graph": str(graph), "reason": "",
    }

    if not graph.exists():
        out["reason"] = ("no .base/graph.nq — this workspace has no BASE tier, "
                         "so there is no firm graph to read")
        return out

    exts = installed_extensions(workspace)
    if exts is None:
        out["reason"] = (
            f"{tier_extensions_dir(workspace)} does not exist, so which "
            "extensions this firm allows cannot be established — refusing to "
            "classify rather than call everything foreign")
        return out

    ws_domains = declared_domains(workspace / ".base" / "domains.toml")
    tier_domains = declared_domains(tier_global_dir(workspace) / "domains.toml")
    if ws_domains is None and tier_domains is None:
        out["reason"] = (
            "neither the workspace nor the firm tier has a readable "
            "domains.toml, so which domains this firm allows cannot be "
            "established — refusing to classify")
        return out

    allowed_domains = (ws_domains or set()) | (tier_domains or set())
    # A domain an allowed extension brings with it is the firm's too: base
    # names it after the extension, `ext:<name>:<something>`.
    ext_prefixes = tuple(f"ext-{_slug(name)}-" for name in exts)

    out["allowed_extensions"] = sorted(exts)
    out["allowed_domains"] = sorted(allowed_domains)

    def _domain_is_firms(name: str) -> bool:
        slug = _slug(name)
        return slug in allowed_domains or slug.startswith(ext_prefixes)

    with graph.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            out["visited"] += 1
            subject = line.split(" ", 1)[0]
            key = ""
            m = _SUBJECT_EXT.search(subject)
            if m:
                if m.group(1) not in exts:
                    key = f"ext/{m.group(1)}"
            else:
                d = _SUBJECT_DOMAIN.search(subject) or _SUBJECT_RULE.search(subject)
                if d and not _domain_is_firms(d.group(1)):
                    kind = "domain" if _SUBJECT_DOMAIN.search(subject) else "rule"
                    key = f"{kind}/{d.group(1)}"
            if key:
                out["foreign"][key] = out["foreign"].get(key, 0) + 1
                out["foreign_lines"] += 1
            else:
                out["firm_lines"] += 1

    if out["visited"] == 0:
        out["reason"] = (
            f"{graph} exists but the census visited 0 lines, so it proved "
            "nothing — this is a failed read, not a clean firm")
    return out


def summary(result: dict[str, Any]) -> str:
    """One line an operator can act on, in the same three states as `Isolation`."""
    if result.get("reason"):
        return f"isolation unknown: {result['reason']}"
    if not result.get("foreign_lines"):
        return (f"clean: {result['visited']} lines read, all of them this firm's")
    worst = sorted(result["foreign"].items(), key=lambda kv: -kv[1])[:3]
    named = ", ".join(f"{k} {n}" for k, n in worst)
    return (f"{result['foreign_lines']} of {result['visited']} lines in this "
            f"firm's graph came from outside the firm ({named})")
