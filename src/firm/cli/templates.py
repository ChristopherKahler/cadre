"""``cadre templates`` — template families that ship inside the package.

Families live in ``firm/templates/<family>/`` and install into a firm
workspace. File routing is by convention:

- ``NN-*.md`` (numbered) files are firm protocols → ``<workspace>/.firm/protocols/``
  (protocols concatenate into every member's run prompt — active immediately)
- everything else (loadout ``*.json`` packs, README/SETUP docs) is staged into
  ``<workspace>/.firm/templates/<family>/`` for the firm's seed script to
  consume (loadout packs merge into contract ``skill_loadout`` — see the
  family's SETUP.md)

Existing destination files are skipped unless ``--force`` — an installed
protocol a firm has since customized is that firm's law, not ours to clobber.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

_PROTOCOL_RE = re.compile(r"^\d{2}-.*\.md$")

_FAMILY_BLURBS = {
    "discipline": "Execution/quality law extracted from PAUL — universal protocol + lead/dev loadout packs",
}


def _templates_root():
    return files("firm") / "templates"


def list_families() -> dict[str, list[str]]:
    """Map of family name → sorted file names shipped in the package."""
    root = _templates_root()
    out: dict[str, list[str]] = {}
    if not root.is_dir():
        return out
    for entry in root.iterdir():
        if entry.is_dir() and not entry.name.startswith("_"):
            out[entry.name] = sorted(f.name for f in entry.iterdir() if f.is_file())
    return out


def run_templates_list() -> int:
    families = list_families()
    if not families:
        print("No template families ship with this build.")
        return 1
    print(f"Template families ({len(families)}):")
    for name, filenames in sorted(families.items()):
        blurb = _FAMILY_BLURBS.get(name, "")
        print(f"\n  {name}" + (f" — {blurb}" if blurb else ""))
        for fn in filenames:
            kind = "protocol → .firm/protocols/" if _PROTOCOL_RE.match(fn) else "staged   → .firm/templates/"
            print(f"    {fn:<32} [{kind}]")
    print("\nInstall one: cadre templates install <family> [--workspace <firm-root>] [--force]")
    return 0


def run_templates_install(family: str, workspace: Path, *, force: bool = False) -> int:
    src = _templates_root() / family
    if not src.is_dir():
        known = ", ".join(sorted(list_families())) or "(none)"
        print(f"Unknown template family {family!r}. Available: {known}")
        return 1

    firm_dir = workspace / ".firm"
    if not firm_dir.is_dir():
        print(f"{workspace} is not a firm workspace (no .firm/ — run `cadre init .` first).")
        return 1

    proto_dir = firm_dir / "protocols"
    stage_dir = firm_dir / "templates" / family
    proto_dir.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    skipped: list[str] = []
    for entry in sorted(src.iterdir(), key=lambda e: e.name):
        if not entry.is_file():
            continue
        dest_dir = proto_dir if _PROTOCOL_RE.match(entry.name) else stage_dir
        dest = dest_dir / entry.name
        if dest.exists() and not force:
            skipped.append(str(dest.relative_to(workspace)))
            continue
        dest.write_bytes(entry.read_bytes())
        installed.append(str(dest.relative_to(workspace)))

    for path in installed:
        print(f"  installed  {path}")
    for path in skipped:
        print(f"  skipped    {path}  (exists — use --force to overwrite)")

    if not installed and not skipped:
        print(f"Family {family!r} contains no files.")
        return 1

    setup = stage_dir / "SETUP.md"
    if setup.exists():
        print(f"\nNext: read {setup.relative_to(workspace)} — attach loadout packs with `cadre templates apply {family} --map <pack>=<CONTRACT-ID>`.")
    return 0


# ---------------------------------------------------------------------------
# apply — merge a family's loadout packs into contracts (the one human call:
# which contract is which role. Everything else is mechanical.)
# ---------------------------------------------------------------------------

def _resolve_pack(family: str, pack_name: str, workspace: Path):
    """Resolve a pack by filename-stem prefix ('dev' → dev-discipline.json).

    Prefers the staged workspace copy (a firm may have customized it); falls
    back to the packaged file. Returns (resolved_name, dict) or (None, None).
    """
    import json

    candidates = []
    stage_dir = workspace / ".firm" / "templates" / family
    if stage_dir.is_dir():
        candidates.extend(p for p in stage_dir.iterdir() if p.name.endswith(".json"))
    pkg_dir = _templates_root() / family
    if pkg_dir.is_dir():
        staged_names = {c.name for c in candidates}
        candidates.extend(
            e for e in pkg_dir.iterdir()
            if e.is_file() and e.name.endswith(".json") and e.name not in staged_names
        )

    matches = {c.name for c in candidates if c.name.startswith(pack_name)}
    if len(matches) != 1:
        return None, sorted(c.name for c in candidates)
    chosen = next(c for c in candidates if c.name in matches)
    return chosen.name, json.loads(chosen.read_text(encoding="utf-8") if hasattr(chosen, "read_text") else chosen.read_bytes().decode())


def merge_pack_into_loadout(loadout: dict, pack: dict) -> int:
    """Append-if-absent merge of a pack's rendering keys. Returns lines added."""
    added = 0
    for key in ("stages", "tools", "duties", "policies"):
        lines = pack.get(key)
        if not lines:
            continue
        existing = loadout.setdefault(key, [])
        for line in lines:
            if line not in existing:
                existing.append(line)
                added += 1
    return added


def run_templates_apply(family: str, workspace: Path, mappings: list[str]) -> int:
    """``cadre templates apply <family> --map dev=CON-ENG --map lead=CON-LEAD``

    Merges each named pack's duties/policies into the named contracts'
    ``skill_loadout`` (append-if-absent — re-applying is a no-op). Multiple
    contracts per pack: ``--map dev=CON-ENG,CON-API``.
    """
    import json

    from firm.core import repo
    from firm.core.db import connect, get_db_path

    firm_dir = workspace / ".firm"
    if not firm_dir.is_dir():
        print(f"{workspace} is not a firm workspace (no .firm/ — run `cadre init .` first).")
        return 1

    plan: list[tuple[str, dict, str]] = []  # (pack_name, pack, contract_id)
    for mapping in mappings:
        if "=" not in mapping:
            print(f"Bad --map {mapping!r} (expected <pack>=<CONTRACT-ID>[,<CONTRACT-ID>...]).")
            return 1
        pack_name, _, contract_csv = mapping.partition("=")
        resolved, pack = _resolve_pack(family, pack_name.strip(), workspace)
        if resolved is None:
            print(f"Pack {pack_name!r} is not a unique prefix of a {family} pack. Available: {', '.join(pack) or '(none)'}")
            return 1
        for contract_id in filter(None, (c.strip() for c in contract_csv.split(","))):
            plan.append((resolved, pack, contract_id))

    if not plan:
        print("Nothing to apply — pass at least one --map <pack>=<CONTRACT-ID>.")
        return 1

    conn = connect(get_db_path(workspace))
    try:
        for pack_name, pack, contract_id in plan:
            row = repo.get(conn, "contract", contract_id)
            if not row:
                print(f"Contract {contract_id!r} not found — nothing written.")
                return 1
        for pack_name, pack, contract_id in plan:
            row = repo.get(conn, "contract", contract_id)
            raw = row.get("skill_loadout")
            loadout = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
            if not isinstance(loadout, dict):
                loadout = {}
            added = merge_pack_into_loadout(loadout, pack)
            if added:
                repo.update(conn, "contract", contract_id, {"skill_loadout": json.dumps(loadout)})
            print(f"  {contract_id}: +{added} line(s) from {pack_name}" + ("" if added else " (already applied)"))
        conn.commit()
    finally:
        conn.close()
    print("Loadout changes take effect on each member's next spawn.")
    return 0
