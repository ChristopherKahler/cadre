"""Seed helpers — the canonical create-or-amend pattern for firm seeds.

A firm's ``scripts/seed_<firm>.py`` is THE definition of the firm
(docs/FIRM-SCAFFOLDING-GUIDE.md). For that promise to be real, a re-run must
be able to AMEND an existing entity, not merely create a missing one. The
``if not repo.get(...): repo.create(...)`` guard every firm independently
invented makes re-runs safe but inert — every edit to an entity that already
exists is silently swallowed.

:func:`ensure` creates when absent and otherwise resyncs ONLY the
definitional fields named in ``resync``. Runtime state — ``status``,
``claimed_by``, ``assignee_member_id`` — is never resynced (and is refused
in ``resync``), so re-seeding a live firm cannot disturb work in flight.
Safe because definitional fields have no runtime write path: no CLI verb,
dashboard action, or MCP tool writes name/description/acceptance_criteria/
depends_on/priority (verified 2026-07-13; see the state-integrity fork).

:func:`seed_session` wraps the whole run: migrations applied, an automatic
``pre-seed`` snapshot (firm.core.snapshot — the 2026-07-12 loss made this
mandatory), one commit on clean exit, rollback on error.

Reference implementation this was promoted from:
``~/firms/crows-and-pawns/scripts/seed_crows_and_pawns.py`` (commit 1c744d0).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from firm.core import repo
from firm.core.db import connect, get_db_path
from firm.core.migrate import apply_migrations
from firm.core.snapshot import take

# Runtime state a seed must NEVER touch. If definitional fields ever grow a
# runtime write path, the seed-vs-db authority question has to be answered
# explicitly before this set shrinks.
#
# pulse_config and budget_config are here because the Board tunes them live:
# the dashboard's per-contract settings write pulse_config.model (the cost
# lever) and budget limits. Resyncing them stripped "model": "sonnet"/"haiku"
# off every dnd-table contract and re-capped a deliberately uncapped
# wastelander budget on 2026-07-13 — both caught only because the pre-seed
# snapshot existed. Goal metric/target also carry live progress on some firms;
# seeds should treat goals as create-only (see FIRM-SCAFFOLDING-GUIDE §3).
RUNTIME_FIELDS = frozenset({
    "status", "claimed_by", "assignee_member_id",
    "pulse_config", "budget_config",
})


def norm(v: Any) -> Any:
    """Canonical form for comparison — json columns come back as ``str`` OR
    already parsed, depending on who wrote them."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    if isinstance(v, str):
        try:
            return json.dumps(json.loads(v), sort_keys=True)
        except (ValueError, TypeError):
            return v
    return v


def ensure(conn: Any, entity: str, data: dict[str, Any],
           resync: tuple[str, ...] = (), *, quiet: bool = False) -> str:
    """Create *entity* if absent; otherwise RESYNC the fields in ``resync``.

    ``resync`` names only DEFINITIONAL fields — what the Board decided this
    thing *is*. Passing runtime state raises. Returns one of
    ``"created" | "resynced" | "exists"`` so seeds can count their work.
    """
    bad = RUNTIME_FIELDS.intersection(resync)
    if bad:
        raise ValueError(
            f"resync must name definitional fields only — {sorted(bad)} is "
            "runtime state; a seed that rewrites it would disturb a live firm")

    row = repo.get(conn, entity, data["id"])
    if row is None:
        repo.create(conn, entity, data)
        if not quiet:
            print(f"  created {entity} {data['id']}")
        return "created"

    changed = {f: data[f] for f in resync if norm(row[f]) != norm(data[f])}
    if changed:
        repo.update(conn, entity, data["id"], changed)
        if not quiet:
            print(f"  RESYNC  {entity} {data['id']} ({', '.join(sorted(changed))})")
        return "resynced"
    if not quiet:
        print(f"  exists  {entity} {data['id']}")
    return "exists"


def merge_pack(conn: Any, contract_id: str, pack_path: str | Path,
               *, quiet: bool = False) -> int:
    """Append a discipline pack's duties/policies into a contract's
    skill_loadout (append-if-absent). Call AFTER the contract's ensure():
    the contract resyncs skill_loadout to its base loadout, packs re-append.
    Order matters — final state is deterministic, and the RESYNC line the
    contract prints on every run is expected churn, not a bug."""
    pack_path = Path(pack_path)
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    row = repo.get(conn, "contract", contract_id)
    if row is None:
        raise ValueError(f"contract {contract_id!r} does not exist — "
                         "ensure() it before merging packs")
    raw = row["skill_loadout"]
    loadout = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
    added = 0
    for key in ("duties", "policies"):
        for line in pack.get(key, []):
            if line not in loadout.setdefault(key, []):
                loadout[key].append(line)
                added += 1
    repo.update(conn, "contract", contract_id, {"skill_loadout": loadout})
    if not quiet:
        print(f"  merged  {pack_path.name} -> {contract_id} (+{added} lines)")
    return added


@contextmanager
def seed_session(workspace: str | Path, *,
                 snapshot_label: str = "pre-seed") -> Iterator[Any]:
    """Connection for a seed run: migrations applied and a pre-seed snapshot
    taken automatically, BEFORE any seed write can land.

    No rollback is promised — ``repo`` commits per call, so a seed that dies
    mid-run has already written. The snapshot is the recovery path: commit it
    and any bad write is a diff, not a loss.
    """
    ws = Path(workspace).expanduser().resolve()
    conn = connect(get_db_path(ws))
    try:
        apply_migrations(conn)
        path = take(conn, ws, label=snapshot_label)
        print(f"  snapshot {path.relative_to(ws)}")
        yield conn
        conn.commit()
    finally:
        conn.close()
