#!/usr/bin/env python3
"""Register chief-of-staff's deliverables that were never on Records.

The firm closed 26 Units and registered 3 Documents. The other 23 deliverables
sat on disk in `deliverables/`, finished and invisible: the Board reviews
Documents, so work nobody could see was work nobody could act on (ESC-026).
`unit.outputs` was NULL firm-wide, including on the three that did have a row.

Root cause was never the missing files. Two things were true at once:

  * No Member could register a deliverable at all — there was no verb. Fixed by
    the `firm doc register` / `firm unit complete --outputs` surface this script
    ships alongside.
  * Seam-4's auto-registration is gated on a contract declaring `file_exists
    require_written`, and NOT ONE chief-of-staff contract declares it. So the
    harness half never fired either.

This script fixes the past. The second cause is live: until CON-001..005 opt in
(a Train/Board judgment, not a mechanical fix), every future pulse re-creates
the gap. `firm doctor`'s `deliverables` check will keep saying so.

The mapping is the filename: `deliverables/UNIT-0NN-*.md` names its Unit. Every
write goes through `register_deliverable`, so version families collapse the way
they do everywhere else — v3 bumps DOC-001 rather than forking a sibling row,
and an older version of a family already carried is skipped, not regressed.
Idempotent: re-running registers nothing new.

Usage:
    python3 scripts/backfill_chief_of_staff_deliverables.py [--live]

Defaults to a dry run — this writes to a live firm's Board-visible state.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from firm.core.db import connect, get_db_path
from firm.core import repo
from firm.services.document import register_deliverable

WORKSPACE = Path("/home/chriskahler/firms/chief-of-staff")
FIRM_ID = "chief-of-staff"
DELIVERABLES = "deliverables"

#: `UNIT-012-armory-capability-report.md` → UNIT-012.
_UNIT_RE = re.compile(r"^(?P<unit>UNIT-\d+)-")


def _plan(conn) -> tuple[list[tuple[str, Path]], list[tuple[Path, str]]]:
    """Pair each deliverable file with its Unit. Returns (pairs, skipped)."""
    pairs: list[tuple[str, Path]] = []
    skipped: list[tuple[Path, str]] = []
    for path in sorted((WORKSPACE / DELIVERABLES).glob("*.md")):
        m = _UNIT_RE.match(path.name)
        if not m:
            # A file that names no Unit cannot be parented by this mapping.
            # `morning-page-2026-07-15.md` is the known case: already DOC-002,
            # registered by hand against UNIT-004 back when that was the only
            # way. Guessing an owner for the rest would put a false deliverable
            # on Records, which is worse than a missing one.
            skipped.append((path, "filename names no Unit"))
            continue
        unit_id = m.group("unit")
        if repo.get(conn, "unit", unit_id) is None:
            skipped.append((path, f"{unit_id} is not a Unit in this firm"))
            continue
        pairs.append((unit_id, path))
    return pairs, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="Actually write. Without it, prints the plan only.")
    args = ap.parse_args()

    conn = connect(get_db_path(WORKSPACE))
    try:
        pairs, skipped = _plan(conn)
        print(f"  {len(pairs)} file(s) map to a Unit; {len(skipped)} skipped\n")
        for path, why in skipped:
            print(f"  skip  {path.name} — {why}")
        if skipped:
            print()

        if not args.live:
            for unit_id, path in pairs:
                print(f"  [dry-run] {unit_id}  ←  {path.name}")
            print(f"\n  dry run — nothing written. Re-run with --live.")
            return 0

        counts: dict[str, int] = {}
        for unit_id, path in pairs:
            unit = repo.get(conn, "unit", unit_id)
            assignee = (unit or {}).get("assignee_member_id")
            if not assignee:
                # Records must carry a Member. A Unit nobody was assigned has
                # no honest actor to credit, and inventing one corrupts the
                # audit trail this whole fork exists to restore.
                counts["no-assignee"] = counts.get("no-assignee", 0) + 1
                print(f"  skip  {path.name} — {unit_id} has no assignee to credit")
                continue
            result = register_deliverable(
                conn, FIRM_ID, unit_id, str(path),
                member_id=assignee, cwd=str(WORKSPACE),
            )
            counts[result["action"]] = counts.get(result["action"], 0) + 1
            doc = result["document"]
            print(f"  {result['action']:11} {doc['id']}  {unit_id}  {path.name}")
        conn.commit()

        docs = repo.find(conn, "document", firm_id=FIRM_ID)
        done = repo.find(conn, "unit", firm_id=FIRM_ID, status="done")
        with_output = [u for u in done if (u.get("outputs") or [])]
        print("\n  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        print(f"  documents on Records: {len(docs)}")
        print(f"  done Units carrying outputs: {len(with_output)}/{len(done)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
