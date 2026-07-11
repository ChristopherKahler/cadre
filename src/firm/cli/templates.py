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
        print(f"\nNext: read {setup.relative_to(workspace)} — loadout packs are merged into contracts by your seed script.")
    return 0
