#!/usr/bin/env python3
"""Proof for #117: nothing outside a firm ever writes into the firm's graph.

The cheap half of this proof is `tests/services/test_firm_graph_isolation.py`
and `tests/services/test_firm_relay_store.py`, which run in CI with no `base`
binary at all. This is the expensive half: it drives the REAL base binary
against real firms, and it breaks its own fix three different ways and requires
a different row to go red for each break.

    ARM A   the defect, reproduced. A firm-shaped workspace whose base resolves
            an operator tier carrying a planted extension and foreign domains.
            It must come out CONTAMINATED. If it ever reads clean, every zero
            in the other arms is worthless and this script refuses to report
            numbers at all.
    ARM B   the lane. A firm founded through Cadre's own code, then given two
            session starts the way a person's Claude session would give them --
            through the env in the firm's own .claude/settings.local.json. It
            must come out with ZERO lines it does not own.
    ARM C   the shipping condition. Arm B against an operator tier that already
            carries several extensions and a fat domains.toml, because a clean
            room is not the machine this ships onto.
    ARM D   the census itself, over a graph seeded with known foreign subjects.
            It must NAME them.
    ARM E   the hub route (#118 DoD 4). One firm founded through
            `founding.commit`, the one call the hub makes, on a machine whose
            ambient tier is planted, then two session starts. Four rows:
            E1  the firm's own tier holds exactly `cadre.toml`, parsed as Cadre's;
            E2  the planted operator tier's extensions keep every byte;
            E3  nothing foreign is in the firm's graph AND Cadre's own entities are;
            E4  `base cadre --help` runs in the firm's tier with no manual step.
            Arms B and C cannot carry these rows: `cadre init` installs no
            extension and writes no exports.

    M1   `_base_env` stops setting BASE_HOME   -> B, C and E1-E4 RED; A, D green
    M2   the census accepts any ext namespace  -> D RED; everything else green
    M3   the firm's tier IS the operator's     -> B, C, D and E1-E4 RED; A green
    M4   `ensure` calls install() with no workspace -> E1-E4 RED; A-D green
    M5   `ensure` never calls install()        -> E1, E3, E4 RED; E2 and A-D green
    M6   `commit` skips base_export.export     -> E3 RED alone

M1 and M3 both redden B and C, and that is stated rather than dressed up: C is
B under a fatter tier, so they are one detector measured twice. The independent
pair is (B/C, D) -- the isolation and the reader that reports on it -- and M2
fires exactly one of them while M1 and M3 fire exactly the other.

In arm E, M4 fires all four rows by construction (the manifest lands in the
operator's tier, so the firm's tier has none, nothing of Cadre's is ingested,
and the command fails), so it is coupled, never a discriminator. M5 fires E1
and leaves E2 green, so E2 is a separate detector from E1. M6 fires E3 alone,
so E3's "Cadre's entities are present" half is separate from E1 and E4.

Nothing here touches the operator's own files. Five of them are hashed in every
home before and after every column, and his extensions directory and
`~/.cadre/sched` are listed; the count checked is printed, and a count of zero is a
failure rather than a pass. The operator's live `firms/seedwin` is never read as a
fixture: it is the live specimen of the defect and it is evidence.

Exit codes:  0 all rows as expected   1 a row disagreed   91 refused to run
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"

def _tiers_to_watch() -> list[Path]:
    """Every home on this machine that could hold a base tier. BOTH SIDES.

    THIS IS THE HOLE THAT LET THIS HARNESS DAMAGE THE OPERATOR'S CONFIG on
    2026-09-14. Run from WSL it watched `Path.home()`, which is the LINUX home,
    while the base binary it had resolved was the WINDOWS exe writing into the
    WINDOWS tier. The tripwire reported `drift=[]` over four scratch workspaces
    registered in the operator's real `base.toml` and three planted extension
    manifests in his real extensions directory. Law 21: a label that names the
    wrong subject turns a true number into a false claim.
    """
    homes = [Path.home()]
    mounted = Path("/mnt/c/Users")
    if mounted.is_dir():
        for candidate in mounted.iterdir():
            if (candidate / ".base-gbl").is_dir() and candidate not in homes:
                homes.append(candidate)
    return homes


def watched() -> tuple[list[Path], list[Path]]:
    """(files to hash, directories to list).

    The directory half is the SECOND hole from the same incident: hashing files
    cannot see a NEW file, and what landed in the operator's extensions
    directory was three new files. A listing catches an addition; a hash of the
    files you already knew about never can.

    `~/.cadre/sched` is listed for arm E. `founding.commit` does not reach the
    scheduler (#118 G0 fact 9), and a founding elsewhere on this machine was
    measured writing a launcher there, so any change in it stops the run.
    """
    files: list[Path] = []
    dirs: list[Path] = []
    for home in _tiers_to_watch():
        files += [home / ".base-gbl" / "base.toml",
                  home / ".base-gbl" / "domains.toml",
                  home / ".base-gbl" / ".base" / "graph.nq",
                  home / ".base" / "graph.nq",
                  home / ".claude" / "CLAUDE.md"]
        dirs += [home / ".base-gbl" / "extensions",
                 home / ".cadre" / "sched"]
    return files, dirs

HOSTILE_FACTS = [{"id": f"hostile-{i}", "text": f"FOREIGNMARKER planted {i}",
                  "when": "2026-09-14 09:00:00"} for i in range(7)]

#: The firm arm E founds. It exists nowhere on this machine except because of
#: this run, so anything named after the firm is attributable (FINGERPRINTS).
FOUNDING_FIRM_ID = "gadwall118e"


def digest(path: Path) -> str:
    if not path.exists():
        return "ABSENT"
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _binary_kind(path: Path) -> str:
    """"elf", "pe", or "other", read from the file's own first bytes."""
    try:
        with path.open("rb") as fh:
            head = fh.read(4)
    except OSError:
        return "other"
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:2] == b"MZ":
        return "pe"
    return "other"


def which_base() -> tuple[Path | None, str]:
    """A base this platform can actually be isolated with, or a refusal.

    `shutil.which("base")` INSIDE WSL RETURNS THE WINDOWS EXE. Measured
    2026-09-14: `which base` gave the Windows exe under the /mnt/c mount, a PE32+
    executable, while an ELF base 0.15.1 sat unused at
    an ELF base sat unused in the Linux home. That matters because a POSIX BASE_HOME
    handed to a Windows base is IGNORED -- Cadre records the measurement in
    `src/firm/sysconfig/binaries.py:92-93` -- so every "isolated" call this
    harness made went to the operator's real Windows tier instead.

    The check is the file's own magic bytes rather than its name or its path:
    the binary that did the damage was called `base`, with no extension, and
    lived on PATH.
    """
    wanted = "elf" if os.name == "posix" else "pe"
    seen: list[str] = []
    candidates: list[Path] = []
    found = shutil.which("base")
    if found:
        candidates.append(Path(found))
    candidates += [Path.home() / ".local" / "bin" / "base",
                   Path.home() / ".local" / "bin" / "base.exe"]
    for candidate in candidates:
        if not candidate.exists():
            continue
        kind = _binary_kind(candidate)
        seen.append(f"{candidate} is {kind}")
        if kind == wanted:
            return candidate, ""
    return None, ("no base binary this platform can be isolated with "
                  f"(wanted {wanted}); found: " + ("; ".join(seen) or "nothing"))


def run(args, env, cwd, stdin_text=None, timeout=300):
    return subprocess.run([str(a) for a in args], env=env, cwd=str(cwd),
                          input=stdin_text, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def session_payload(cwd: Path, tag: str) -> str:
    return json.dumps({"session_id": f"whimbrel-117-{tag}",
                       "transcript_path": str(cwd / "transcript.jsonl"),
                       "cwd": str(cwd), "hook_event_name": "SessionStart",
                       "source": "startup"})


def manifest(name: str, root: Path) -> str:
    framework = (root / f"{name}_fw").as_posix()
    state = (root / f"{name}_state").as_posix()
    return (f'[extension]\nname = "{name}"\nversion = "0.1.0"\n'
            f'description = "planted by the #117 acceptance harness"\n'
            f'framework_dir = "{framework}"\n\nstate_dir = "{state}"\n\n'
            f'[[commands]]\nname = "{name}probe"\nhandler = "bin/{name}probe"\n'
            f'description = "planted"\nusage = "base {name}probe"\n\n'
            f'[[hooks.session_start.ingest]]\nfile = "facts.json"\n'
            f'entity = "PlantedFact"\nstrategy = "upsert"\n'
            f'domain = "planted-{name}"\n')


def build_operator_tier(root: Path, base: Path, names: list[str],
                        domains: int) -> Path:
    """A tier that plays "this machine already has extensions installed".

    Never the operator's real one. This is what makes the whole harness safe to
    run on his desktop.
    """
    tier = root / "operator-tier"
    tier.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["BASE_HOME"] = str(tier)
    for name in names:
        (root / f"{name}_fw").mkdir(parents=True, exist_ok=True)
        (root / f"{name}_state").mkdir(parents=True, exist_ok=True)
        (root / f"{name}_state" / "facts.json").write_text(
            json.dumps(HOSTILE_FACTS, indent=1), encoding="utf-8")
        path = root / f"{name}.toml"
        path.write_text(manifest(name, root), encoding="utf-8")
        installed = run([base, "extension", "install", str(path)], env, root)
        if installed.returncode != 0:
            raise SystemExit(f"could not plant {name}: "
                             f"{(installed.stderr or installed.stdout)[:300]}")
    if domains:
        block = "".join(
            f'[[domain]]\nname = "foreign-{i}"\nmode = "triggered"\n'
            f'prompt_keywords = ["foreign {i}"]\n'
            f'rules = ["a rule that belongs to the operator, not to any firm"]\n\n'
            for i in range(domains))
        (tier / ".base-gbl").mkdir(parents=True, exist_ok=True)
        (tier / ".base-gbl" / "domains.toml").write_text(block, encoding="utf-8")
    return tier


def census_with(src: Path, workspace: Path) -> dict:
    """Run THIS tree's census in a child interpreter, against *src*.

    A child, because the mutation arms need a different `src` on sys.path and
    an in-process import would be pinned to whichever tree got there first.
    """
    code = ("import json,sys;"
            "from firm.services.graph_isolation import census;"
            f"print(json.dumps(census({str(workspace)!r})))")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    proc = run([sys.executable, "-c", code], env, workspace.parent)
    if proc.returncode != 0:
        return {"reason": f"the census did not run: {(proc.stderr or '')[-300:]}",
                "visited": 0, "foreign": {}, "foreign_lines": 0}
    return json.loads((proc.stdout or "{}").strip().splitlines()[-1])


def found_firm(src: Path, root: Path, name: str, operator_tier: Path,
               base: Path) -> tuple[Path, str]:
    """Found a firm through Cadre's own code, on a machine whose global tier is
    the planted one. Returns the firm and whatever went wrong, or ""."""
    workspace = root / name
    workspace.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    env["BASE_HOME"] = str(operator_tier)      # the ambient tier Cadre must beat
    env.pop("CADRE_NO_BASE", None)
    # The child resolves `base` off PATH, and inside WSL the first hit is the
    # WINDOWS exe on /mnt/c. Cadre then refuses to scaffold, correctly (#87):
    # "a base built for another platform still executes here and ignores
    # BASE_HOME". Measured 2026-09-14 -- with the inherited PATH the firm got
    # no tier at all and arms B and C had nothing to read. Putting the
    # platform's own base first is what a real user on this host has.
    env["PATH"] = str(base.parent) + os.pathsep + (env.get("PATH") or "")
    proc = run([sys.executable, "-m", "firm", "init", str(workspace), "--demo"],
               env, root)
    if proc.returncode != 0:
        return workspace, (f"cadre init exited {proc.returncode}: "
                           f"{(proc.stderr or proc.stdout or '')[-400:]}")
    if not (workspace / ".base").is_dir():
        # `cadre init` exits 0 when it SKIPS the tier -- base absent is a
        # supported state. Quote what it said rather than letting the next arm
        # report "no graph", which names the symptom and hides the cause.
        said = [line.strip() for line in (proc.stdout or "").splitlines()
                if "skipped:" in line or "scaffold" in line]
        return workspace, ("cadre init exited 0 but scaffolded no tier: "
                           + (" | ".join(said) or "and said nothing about it"))
    return workspace, ""


def session_env_from_settings(workspace: Path) -> tuple[dict, str]:
    """The env a real Claude session would carry into its hook children.

    Measured 2026-09-14 with a real headless run: a project
    `.claude/settings.local.json` `env` block reaches the hook child, which is
    why this is the faithful way to drive arm B rather than a convenience.
    """
    path = workspace / ".claude" / "settings.local.json"
    env = dict(os.environ)
    if not path.exists():
        return env, f"{path} does not exist, so a session here inherits nothing"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return env, f"{path} unreadable: {exc}"
    for key, value in (settings.get("env") or {}).items():
        env[str(key)] = str(value)
    return env, ""


def graph_lines(workspace: Path) -> int:
    graph = workspace / ".base" / "graph.nq"
    if not graph.exists():
        return 0
    with graph.open(encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if line.strip())


# --- the arms ---------------------------------------------------------------


def arm_a(root: Path, base: Path, operator_tier: Path) -> dict:
    """The defect. No Cadre in the loop: base standing in a firm-shaped
    workspace with the operator's tier is what wrote 13,564 lines into
    firms/seedwin."""
    workspace = root / "armA"
    (workspace / ".firm").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["BASE_HOME"] = str(operator_tier)
    run([base, "scaffold", str(workspace)], env, root)
    for i in (1, 2):
        run([base, "hook", "session-start"], env, workspace,
            stdin_text=session_payload(workspace, f"a{i}"))
    graph = workspace / ".base" / "graph.nq"
    text = graph.read_text(encoding="utf-8", errors="replace") if graph.exists() else ""
    planted = text.count("FOREIGNMARKER") + text.count("ext/hostile/")
    foreign_domains = text.count("domain/foreign-")
    return {"lines": graph_lines(workspace), "planted": planted,
            "foreign_domains": foreign_domains,
            "pass": planted > 0 or foreign_domains > 0,
            "why": "the defect must reproduce, or every zero below is untrustworthy"}


def arm_isolated(src: Path, root: Path, base: Path, operator_tier: Path,
                 name: str) -> dict:
    """A firm founded through Cadre, then given two session starts the way a
    person's Claude session gives them."""
    workspace, failure = found_firm(src, root, name, operator_tier, base)
    if failure:
        return {"pass": False, "why": failure, "lines": 0, "foreign": {},
                "visited": 0, "tier": ""}
    env, detail = session_env_from_settings(workspace)
    for i in (1, 2):
        run([base, "hook", "session-start"], env, workspace,
            stdin_text=session_payload(workspace, f"{name}{i}"))
    result = census_with(src, workspace)
    tier = env.get("BASE_HOME", "")
    inside = str(workspace) in tier
    return {"pass": (not result.get("reason")
                     and result.get("foreign_lines", 1) == 0
                     and inside),
            "why": (result.get("reason")
                    or ("" if inside else
                        f"the session env points at {tier or 'nothing'}, which "
                        "is not inside this firm")
                    or detail),
            "lines": graph_lines(workspace),
            "visited": result.get("visited", 0),
            "foreign": result.get("foreign", {}),
            "tier": tier}


def arm_d(src: Path, root: Path) -> dict:
    """The census, over a graph whose foreign lines are known by construction."""
    workspace = root / "armD"
    (workspace / ".base").mkdir(parents=True, exist_ok=True)
    ext_dir = workspace / ".firm" / "base-home" / ".base-gbl" / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "cadre.toml").write_text("[extension]\n", encoding="utf-8")
    (workspace / ".base" / "domains.toml").write_text(
        '[[domain]]\nname = "acme"\n', encoding="utf-8")
    line = ('<http://ops-sys.local/ontology#{s}> '
            '<http://ops-sys.local/ontology#name> "x" '
            '<http://ops-sys.local/ontology#graph/ws/acme> .\n')
    subjects = ["ext/cadre/CadreMember/mem-001", "domain/acme",
                "note/a-member-wrote-this",
                "ext/hostile/PlantedFact/1", "ext/whatever/Thing/2",
                "domain/foreign-0", "rule/foreign-0/1"]
    (workspace / ".base" / "graph.nq").write_text(
        "".join(line.format(s=s) for s in subjects), encoding="utf-8")
    result = census_with(src, workspace)
    named = set(result.get("foreign", {}))
    expected = {"ext/hostile", "ext/whatever", "domain/foreign-0", "rule/foreign-0"}
    return {"pass": named == expected and result.get("visited") == len(subjects),
            "why": (result.get("reason")
                    or (f"named {sorted(named)}, expected {sorted(expected)}"
                        if named != expected else "")),
            "visited": result.get("visited", 0), "foreign": result.get("foreign", {})}


def _founding_proposal(fid: str) -> dict:
    """`_proposal` in tests/test_founding_validates_base.py, value for value."""
    return {
        "firm_id": fid,
        "name": "Zed Base",
        "premise": "a firm for measuring the base wiring with",
        "north_star": {"target": "prove founding checks base"},
        "operations": [{"name": "Running it", "purpose": "keep it running"}],
        "members": [{"name": "Vantage", "role": "Chief of Staff",
                     "owns": "everything", "operation": "Running it",
                     "leads": True, "model": "sonnet",
                     "skills": [], "gates": []}],
    }


#: The founding child. It names the tree that answered: the venv this harness
#: runs under carries an editable install of ANOTHER checkout, and a number
#: from that tree is a true number about the wrong code.
_FOUND_CODE = """
import json, sys
from pathlib import Path
import firm
from firm.dashboard import founding
out = founding.commit(Path(sys.argv[2]), json.loads(sys.argv[1]))
ready = out.get("base_ready") or {}
install = ready.get("install") or {}
exported = out.get("base_export") or {}
print("ARM-E-RESULT " + json.dumps({
    "firm_file": firm.__file__,
    "ok": out.get("ok"),
    "error": out.get("error", ""),
    "base_cadre_runs": out.get("base_cadre_runs"),
    "repaired": ready.get("repaired"),
    "repair": ready.get("repair", ""),
    "ready_reason": ready.get("reason", ""),
    "install_ok": install.get("ok"),
    "install_reason": install.get("reason", ""),
    "exported": len(exported.get("written") or []),
    "wire_live": (out.get("base_wire") or {}).get("live"),
}, default=str))
"""


def found_through_the_hub(src: Path, root: Path, fid: str, operator_tier: Path,
                          base: Path) -> tuple[Path, dict, str]:
    """Found a firm the way the hub does: `founding.commit`, in a child.

    `commit` has one caller in the product, `dashboard/server.py`, and this is
    that call with the web server taken out. Returns the workspace, what the
    child reported, and what went wrong or "".
    """
    firms = root / "armE"
    firms.mkdir(parents=True, exist_ok=True)
    workspace = firms / fid
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    env["BASE_HOME"] = str(operator_tier)      # the ambient tier founding must beat
    env.pop("CADRE_NO_BASE", None)
    # This platform's base first, as found_firm has it, then THIS interpreter's
    # bin. A source checkout's manifest handler is bin/cadre, and that shim
    # execs `cadre` off PATH (PR 127 G2), so without the venv on PATH install()'s
    # own `base cadre --help` fails and every E row measures the shell.
    env["PATH"] = os.pathsep.join(
        [str(base.parent), str(Path(sys.executable).parent), env.get("PATH") or ""])
    proc = run([sys.executable, "-c", _FOUND_CODE,
                json.dumps(_founding_proposal(fid)), str(firms)],
               env, firms, timeout=900)
    said = [line for line in (proc.stdout or "").splitlines()
            if line.startswith("ARM-E-RESULT ")]
    if proc.returncode != 0 or not said:
        return workspace, {}, (
            f"founding.commit did not report (rc {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '')[-400:]}")
    report = json.loads(said[-1][len("ARM-E-RESULT "):])
    try:
        Path(report.get("firm_file") or "").resolve().relative_to(src.resolve())
    except ValueError:
        return workspace, report, (
            f"the child imported firm from {report.get('firm_file')}, not from "
            f"{src}, so every E row would describe another tree")
    if report.get("ok") is not True:
        return workspace, report, (
            f"founding.commit returned ok={report.get('ok')}: {report.get('error')}")
    return workspace, report, ""


_CADRE_ENTITIES = ("CadreMember", "CadreUnit", "CadreGate")
#: base's shape for an ingested entity, measured read-only on seedwin's graph
#: 2026-09-14: `ontology#ext/cadre/CadreMember/<id>` and `.../CadreUnit/<id>`.
_CADRE_SUBJECT = re.compile(r"ontology#ext/cadre/([^/>]+)/")


def _tier_bytes(directory: Path) -> dict[str, str]:
    """name -> md5 of every file directly in *directory*; {} when it is absent."""
    if not directory.is_dir():
        return {}
    return {p.name: digest(p) for p in sorted(directory.iterdir()) if p.is_file()}


def arm_e(src: Path, root: Path, base: Path, operator_tier: Path) -> dict:
    """One firm founded through the hub route, then two session starts.

    Every path these rows read is built HERE from the workspace, never asked of
    the product, so a mutation that moves the firm's tier cannot move the
    instrument with it (law 42).
    """
    operator_ext = operator_tier / ".base-gbl" / "extensions"
    before = _tier_bytes(operator_ext)
    workspace, report, failure = found_through_the_hub(
        src, root, FOUNDING_FIRM_ID, operator_tier, base)
    out: dict = {"founding_failure": failure, "report": report}

    # E1, DoD-1 through founding: the firm's own tier holds exactly cadre.toml,
    # and the file is Cadre's by its parsed [extension] name.
    tier_root = workspace / ".firm" / "base-home"
    firm_ext = tier_root / ".base-gbl" / "extensions"
    listing = sorted(p.name for p in firm_ext.iterdir()) if firm_ext.is_dir() else []
    name = ""
    if listing == ["cadre.toml"]:
        try:
            parsed = tomllib.loads((firm_ext / "cadre.toml").read_bytes().decode("utf-8"))
            name = str((parsed.get("extension") or {}).get("name") or "")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            name = f"unreadable: {exc}"
    out["E1"] = {"pass": listing == ["cadre.toml"] and name == "cadre",
                 "listing": listing, "name": name}

    # E2, DoD-2 through founding: the planted operator tier's extensions keep
    # their bytes. Visiting nothing proves nothing, so zero visited fails.
    after = _tier_bytes(operator_ext)
    changed = sorted(n for n in set(before) | set(after)
                     if before.get(n) != after.get(n))
    out["E2"] = {"pass": bool(before) and not changed,
                 "visited": len(before), "changed": changed}

    # Two session starts, given exactly the way arms B and C give them.
    env, detail = session_env_from_settings(workspace)
    for i in (1, 2):
        run([base, "hook", "session-start"], env, workspace,
            stdin_text=session_payload(workspace, f"armE{i}"))

    # E3, DoD-3: nothing foreign in the firm's graph, AND Cadre's own entities
    # are there. "Present" counts the three entities the manifest ingests, not
    # any subject under ext/cadre/, so the row cannot pass on something base
    # writes for an installed extension whose state files are missing.
    result = census_with(src, workspace)
    seen: dict[str, set[str]] = {}
    graph = workspace / ".base" / "graph.nq"
    if graph.exists():
        with graph.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                subject = line.split(" ", 1)[0]
                m = _CADRE_SUBJECT.search(subject)
                if m:
                    seen.setdefault(m.group(1), set()).add(subject)
    counts = {k: len(v) for k, v in sorted(seen.items())}
    declared = sum(counts.get(e, 0) for e in _CADRE_ENTITIES)
    clean = not result.get("reason") and result.get("foreign_lines", 1) == 0
    out["E3"] = {"pass": clean and declared >= 1,
                 "why": result.get("reason") or detail,
                 "lines": graph_lines(workspace),
                 "visited": result.get("visited", 0),
                 "foreign": result.get("foreign", {}),
                 "cadre_subjects": counts, "declared": declared,
                 "tier": env.get("BASE_HOME", "")}

    # E4, #118 DoD 4: `base cadre` runs in the firm's own tier straight after
    # founding, with no manual install. The tier root is made first if it is
    # missing, because a Member's spawn makes it (_base_env -> ensure_tier), and
    # a BASE_HOME naming nothing asks about base's fallback, not about this firm.
    tier_root.mkdir(parents=True, exist_ok=True)
    member = dict(os.environ)
    member["BASE_HOME"] = str(tier_root)
    member["PATH"] = os.pathsep.join(
        [str(base.parent), str(Path(sys.executable).parent), member.get("PATH") or ""])
    ran = run([base, "cadre", "--help"], member, root, timeout=120)
    said = [line.strip() for line in (ran.stderr or ran.stdout or "").splitlines()
            if line.strip()]
    out["E4"] = {"pass": ran.returncode == 0, "rc": ran.returncode,
                 "said": said[0][:160] if said else ""}
    return out


# --- mutations --------------------------------------------------------------

MUTATIONS = {
    "M1": ("_base_env stops setting BASE_HOME",
           "src/firm/services/base_domain.py",
           '        env["BASE_HOME"] = str(ensure_tier(workspace))',
           '        ensure_tier(workspace)'),
    "M2": ("the census accepts any ext namespace",
           "src/firm/services/graph_isolation.py",
           "                if m.group(1) not in exts:",
           "                if False:"),
    "M3": ("the firm's tier IS the operator's home",
           "src/firm/services/graph_isolation.py",
           '    return _as_path(workspace) / ".firm" / TIER_DIRNAME',
           '    import os as _os\n'
           '    return Path(_os.environ.get("BASE_HOME") or Path.home())'),
    "M4": ("ensure calls install() with no workspace",
           "src/firm/services/base_ready.py",
           '        outcome = base_extension.install(workspace=workspace)',
           '        outcome = base_extension.install()'),
    "M5": ("ensure never calls install() (PR 127's shape)",
           "src/firm/services/base_ready.py",
           '        outcome = base_extension.install(workspace=workspace)',
           '        outcome = {}'),
    "M6": ("commit skips base_export.export",
           "src/firm/dashboard/founding.py",
           '    exported = base_export.export(workspace, fid)',
           '    exported = {}'),
}


def mutated_src(root: Path, key: str) -> tuple[Path, str]:
    """A COPY of src with one thing broken. The worktree is never edited."""
    label, rel, old, new = MUTATIONS[key]
    destination = root / f"src-{key}"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(SRC, destination)
    target = destination / Path(rel).relative_to("src")
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        return destination, (f"{key}: the line it breaks occurs "
                             f"{text.count(old)} times, so this mutation did "
                             "not land and its row proves nothing")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return destination, ""


def snapshot(files: list[Path], dirs: list[Path]) -> dict[str, str]:
    """Hashes for files, LISTINGS for directories.

    osprey's sentence, kept verbatim because it is the whole lesson of the
    2026-09-14 incident: *a tripwire that only sees changes to files it already
    knows about cannot see an addition.* Three planted manifests appeared in the
    operator's extensions directory and every hash in the old snapshot was
    unchanged, because none of those hashes was of a file that had not existed
    before.
    """
    state = {str(p): digest(p) for p in files}
    for directory in dirs:
        if directory.is_dir():
            entries = sorted(f"{p.name}:{p.stat().st_size}"
                             for p in directory.iterdir() if p.is_file())
            state[f"dir:{directory}"] = "|".join(entries)
        else:
            state[f"dir:{directory}"] = "ABSENT"
    return state


#: Strings that exist nowhere on this machine except because of this run.
FINGERPRINTS = ("FOREIGNMARKER", "whimbrel-117-", "planted by the #117",
                "hostile.toml", "second.toml", "third.toml", FOUNDING_FIRM_ID)


def _marks(root: Path) -> tuple[str, ...]:
    return tuple(FINGERPRINTS) + (root.name,)


def baseline_of(key: str, root: Path) -> dict[str, int] | list[str] | None:
    """What attribution compares a watched thing against later.

    A file: how often each of this run's marks occurs in its body, zero for
    every mark when the file is absent, None when it exists and cannot be
    read. A directory (a `dir:` key): the names of its entries, [] when absent.
    """
    path = Path(key[4:] if key.startswith("dir:") else key)
    if key.startswith("dir:"):
        return sorted(p.name for p in path.iterdir()) if path.is_dir() else []
    marks = _marks(root)
    if not path.exists():
        return {m: 0 for m in marks}
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {m: body.count(m) for m in marks}


def attribute(drift: list[str], root: Path,
              baseline: dict[str, dict[str, int] | list[str] | None],
              ) -> tuple[list[str], list[str]]:
    """Split drift into "this run did it" and "something else did".

    A tripwire over SHARED LIVE STATE has to attribute, not merely detect.
    `~/.base/graph.nq` is the operator's own workspace graph and every base
    command from every session on this machine touches it -- including the
    relay pings this session sends between arms. A tripwire that fails on any
    movement there reports a leak on every concurrent session, and a tripwire
    that stops watching it cannot see a real one. So the test is CONTENT: does
    the file now carry something that exists only because this run existed?

    Measured 2026-09-14: the operator's Windows-side workspace graph moved during arm
    M2 of a run that never named a Windows path, while another builder was
    working in that workspace.

    WHAT IS NEW, NEVER WHAT IS PRESENT. Measured 2026-09-14 on the first arm E
    run: the operator's Windows global graph already carried `whimbrel-117-`,
    `hostile.toml`, `second.toml` and `third.toml`, in four relay pings about
    that morning's incident. So another session's relay write during column M6
    was called this run's leak and stopped the run, while this run's own root
    name occurred 0 times in the file. So a file is this run's only when a mark
    occurs MORE OFTEN than in *baseline* (taken with the snapshot), and a
    directory only when an entry that was not there before carries a mark.
    """
    mine: list[str] = []
    theirs: list[str] = []
    marks = _marks(root)
    for key in drift:
        now = baseline_of(key, root)
        was = baseline.get(key)
        if key.startswith("dir:"):
            new = set(now or []) - set(was or [])
            (mine if any(m in name for name in new for m in marks)
             else theirs).append(key)
            continue
        if now is None:
            mine.append(key)        # cannot read it to clear it: assume ours
            continue
        counted = was if isinstance(was, dict) else {}
        grew = [m for m, n in now.items() if n > counted.get(m, 0)]
        (mine if grew else theirs).append(key)
    return mine, theirs


def main() -> int:
    base, refusal = which_base()
    if base is None:
        print("REFUSED:", refusal)
        return 91
    if not SRC.is_dir():
        print(f"REFUSED: {SRC} is not a directory")
        return 91

    # Second, independent refusal, and it is the product's own guard rather
    # than a copy of it: `base_can_honour_tier` is what `scaffold_tier` and
    # `extension install` ask before they let a resolved binary near a tier
    # (#75, #87). A harness that skips the check its own product makes is a
    # harness measuring something the product would never do.
    sys.path.insert(0, str(SRC))
    try:
        from firm.sysconfig.binaries import base_can_honour_tier

        may_run, why = base_can_honour_tier(
            str(base), Path(tempfile.gettempdir()) / "whimbrel-117-tier-probe")
        if not may_run:
            print("REFUSED by Cadre's own guard:", why)
            return 91
    except ImportError as exc:
        print(f"REFUSED: cannot import the product under test from {SRC}: {exc}")
        return 91

    print("=== provenance ===")
    print("repo        :", REPO)
    print("src         :", SRC)
    print("base        :", base, digest(base))
    version = run([base, "--version"], dict(os.environ), REPO)
    print("base version:", (version.stdout or version.stderr).strip())
    print("python      :", sys.executable, sys.version.split()[0])
    print("cwd control : every arm runs with cwd inside its own scratch root")

    root = Path(tempfile.mkdtemp(prefix="whimbrel-117-"))
    print("scratch root:", root)

    watch_files, watch_dirs = watched()
    before = snapshot(watch_files, watch_dirs)
    # Taken at the same moment as the snapshot: drift is attributed by marks
    # that are NEW, never by marks the operator's files already carried.
    marked = {key: baseline_of(key, root) for key in before}
    print("tripwire watches:", len(watch_files), "files and", len(watch_dirs),
          "directories, across these homes:",
          ", ".join(str(h) for h in _tiers_to_watch()))
    if len(before) < 2:
        print("REFUSED: the tripwire watches almost nothing, so it proves nothing")
        return 91

    rows: dict[str, dict[str, bool]] = {}
    details: list[str] = []
    failures: list[str] = []

    columns = ["ORIG", "M1", "M2", "M3", "M4", "M5", "M6"]
    for column in columns:
        if column == "ORIG":
            src, problem = SRC, ""
        else:
            src, problem = mutated_src(root, column)
        if problem:
            failures.append(problem)
            continue

        run_root = root / column
        run_root.mkdir(parents=True, exist_ok=True)
        plain_tier = build_operator_tier(run_root / "plain", base,
                                         ["hostile"], 1)
        fat_tier = build_operator_tier(run_root / "fat", base,
                                       ["hostile", "second", "third"], 6)
        # Arm E's own planted tier, in the fat shape: its E2 snapshot visits
        # several files, and no other arm shares the directory E2 compares.
        founding_tier = build_operator_tier(run_root / "founding", base,
                                            ["hostile", "second", "third"], 6)

        a = arm_a(run_root, base, plain_tier)
        b = arm_isolated(src, run_root, base, plain_tier, "armB")
        c = arm_isolated(src, run_root, base, fat_tier, "armC")
        d = arm_d(src, run_root)
        e = arm_e(src, run_root, base, founding_tier)
        if e["founding_failure"]:
            # A founding that did not complete reads F on every E row, which
            # would MATCH a column predicted F for the wrong reason. So it
            # fails the run outright, whatever the column predicts.
            failures.append(f"{column}/E: the founding did not complete, so no "
                            f"E row in this column is evidence: "
                            f"{e['founding_failure']}")

        rows[column] = {"A": a["pass"], "B": b["pass"], "C": c["pass"],
                        "D": d["pass"], "E1": e["E1"]["pass"],
                        "E2": e["E2"]["pass"], "E3": e["E3"]["pass"],
                        "E4": e["E4"]["pass"]}
        r = e["report"]
        details.append(
            f"[{column}] A lines={a['lines']} planted={a['planted']} "
            f"foreign_domains={a['foreign_domains']} pass={a['pass']}\n"
            f"[{column}] B lines={b['lines']} visited={b['visited']} "
            f"foreign={b['foreign']} pass={b['pass']} {b['why']}\n"
            f"[{column}] C lines={c['lines']} visited={c['visited']} "
            f"foreign={c['foreign']} pass={c['pass']} {c['why']}\n"
            f"[{column}] D visited={d['visited']} foreign={d['foreign']} "
            f"pass={d['pass']} {d['why']}\n"
            f"[{column}] E founded ok={r.get('ok')} "
            f"base_cadre_runs={r.get('base_cadre_runs')} "
            f"repaired={r.get('repaired')} exported={r.get('exported')} "
            f"wire_live={r.get('wire_live')} firm={r.get('firm_file')} "
            f"{e['founding_failure']}\n"
            f"[{column}]   install ok={r.get('install_ok')} "
            f"said={r.get('install_reason')!r} repair={r.get('repair')!r}\n"
            f"[{column}] E1 listing={e['E1']['listing']} "
            f"name={e['E1']['name']!r} pass={e['E1']['pass']}\n"
            f"[{column}] E2 visited={e['E2']['visited']} "
            f"changed={e['E2']['changed']} pass={e['E2']['pass']}\n"
            f"[{column}] E3 lines={e['E3']['lines']} "
            f"visited={e['E3']['visited']} foreign={e['E3']['foreign']} "
            f"cadre_subjects={e['E3']['cadre_subjects']} "
            f"declared={e['E3']['declared']} tier={e['E3']['tier']} "
            f"pass={e['E3']['pass']} {e['E3']['why']}\n"
            f"[{column}] E4 rc={e['E4']['rc']} said={e['E4']['said']!r} "
            f"pass={e['E4']['pass']}")

        after = snapshot(watch_files, watch_dirs)
        drift = [k for k, v in before.items() if after.get(k) != v]
        mine, theirs = attribute(drift, root, marked)
        if theirs:
            print(f"  [{column}] these moved while this ran, and carry nothing "
                  f"new of this run's: {theirs}")
            before = after          # re-baseline; another session owns those
            for key in theirs:
                marked[key] = baseline_of(key, root)
        if mine:
            failures.append(f"{column}: THIS RUN LEAKED INTO: {mine}")
            print(f"  STOPPING after {column}: {mine}")
            break      # never run another arm over a machine already dirtied

    print("\n=== what each arm read ===")
    for line in details:
        print(line)

    print("\n=== the matrix (True = that arm's own claim held) ===")
    print(f"{'arm':4} " + " ".join(f"{c:>6}" for c in columns))
    for arm in ("A", "B", "C", "D", "E1", "E2", "E3", "E4"):
        print(f"{arm:4} " + " ".join(
            f"{str(rows.get(c, {}).get(arm, 'n/a')):>6}" for c in columns))

    # The control comes first: a blind arm A voids every zero below it.
    if not rows.get("ORIG", {}).get("A"):
        print("\nREFUSED: arm A did not reproduce the defect, so no zero in this "
              "run is evidence of anything. Printing no verdict.")
        return 91

    expected = {
        "ORIG": {"A": True, "B": True, "C": True, "D": True,
                 "E1": True, "E2": True, "E3": True, "E4": True},
        # M1/E3 is False, and not for the reason first drafted. The firm's
        # settings.local.json gets its BASE_HOME from write_session_env, not
        # from _base_env, so M1 does not move the session's tier. It moves
        # founding's install: that runs with the child's own BASE_HOME, the
        # planted tier (E2), so no cadre.toml reaches the firm's tier (E1, E4)
        # and the session starts find no Cadre extension to ingest (E3).
        "M1": {"A": True, "B": False, "C": False, "D": True,
               "E1": False, "E2": False, "E3": False, "E4": False},
        "M2": {"A": True, "B": True, "C": True, "D": False,
               "E1": True, "E2": True, "E3": True, "E4": True},
        # M3/D is False and that is a statement about the mutation, not a
        # softened expectation. M3 moves the TIER, and the tier is the one
        # thing both detectors read: the isolation writes into it and the
        # census reads its allow-list out of it. So M3 cannot separate them --
        # it fires both, which under law 39 means it is not the mutation that
        # proves independence. M1 (isolation only) and M2 (census only) are the
        # pair that does, and they are each required to fire exactly one side
        # above. M3 earns its place by being the failure that looks normal:
        # a firm quietly using the operator's tier, which is what B and C must
        # go red over. Arm E under M3: founding installs into the planted tier
        # (E2), the literal firm tier holds nothing (E1, E4), and the census,
        # its allow-list moved to a home tier with neither Cadre nor the planted
        # extensions, names the ingested lines foreign (E3).
        "M3": {"A": True, "B": False, "C": False, "D": False,
               "E1": False, "E2": False, "E3": False, "E4": False},
        # Coupled by construction, never a discriminator: the manifest lands in
        # the planted tier, so the firm's tier has none and nothing of Cadre's
        # is ingested or runs.
        "M4": {"A": True, "B": True, "C": True, "D": True,
               "E1": False, "E2": False, "E3": False, "E4": False},
        # E2 stays green while E1 goes red: E2 is a separate detector.
        "M5": {"A": True, "B": True, "C": True, "D": True,
               "E1": False, "E2": True, "E3": False, "E4": False},
        # E3 alone: its "Cadre's entities are present" half is separate from
        # the install rows.
        "M6": {"A": True, "B": True, "C": True, "D": True,
               "E1": True, "E2": True, "E3": False, "E4": True},
    }
    for column, wanted in expected.items():
        got = rows.get(column)
        if got is None:
            failures.append(f"{column}: never ran")
            continue
        for arm, want in wanted.items():
            if got.get(arm) is not want:
                failures.append(
                    f"{column}/{arm}: expected {want}, got {got.get(arm)}")

    print("\n=== failures ===")
    for failure in failures:
        print("  FAIL:", failure)
    if not failures:
        print("  none")
    rc = 1 if failures else 0
    print("\nscratch root left in place for reading:", root)
    print("verify rc:", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
