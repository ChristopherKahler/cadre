"""Write the firm's state where base's ingest is already looking for it.

The shipped manifest has always declared three ingest sources and three
re-ingest handlers under ``.firm/base-export/``. **Nothing ever wrote them.**
Measured at main ``89bcf3208abc``: the only producer anywhere on the machine was
the fixture inside ``scripts/verify/verify_ext_install.sh``, which writes the
files so that its own probe has something to ingest. So base opened three paths
that did not exist, the graph got nothing, and a real firm's roster, units and
gates never became queryable — while a probe kept proving the mechanism worked.

**A selector finds sites that QUOTE a string. It cannot find the absence of a
writer.** A test asserted the manifest declares these paths; the manifest quotes
these paths; nobody ever asked who writes them. That is the whole reason this
module did not exist for two months.

THE PATHS ARE NOT WRITTEN DOWN HERE. ``declared_exports()`` parses the shipped
manifest and returns what it finds. Restating ``members.json`` in this file would
put a literal on one side of a contract whose other side moves, which is issue
#35's family: rename the path in the manifest and the exporter would go on
writing a file nobody reads, with every test still green. Deriving it means a
rename drags this module along or fails loudly.

**The row shape is not written down either.** ``repo.find`` returns whatever
columns the table actually has, and that is what gets serialised. A hand-picked
field list is the same frozen literal one layer down: add a column to ``unit``
and the export silently stops carrying it. The shape base accepts — a JSON array
of flat objects each carrying an ``id`` — was read off a working producer
(``scripts/verify/verify_ext_install.sh:101-106``, whose result is read back out
of ``graph.nq`` at lines 113-117) and off a second extension using the same
contract in production (``~/.base-gbl/extensions/outpost.toml:20-28``).

**Writes are atomic.** The manifest's ``post_tool`` handlers fire on a write to
exactly these paths, so a reader can arrive mid-write; a half-written array would
be re-ingested as truth. ``os.replace`` over a temp file in the same directory
makes the swap indivisible.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

# The one thing that cannot be derived: the manifest speaks in entity names and
# the database speaks in table names, and nothing connects them but this.
#
# A literal against a moving thing is only safe when something fails the moment
# they drift apart, so this map is guarded FROM BOTH DIRECTIONS in
# tests/services/test_base_export.py — every entity the manifest declares has a
# producer here, and every producer here is declared by the manifest. Adding an
# ingest block with no producer then fails the suite, which is this exact defect
# made unrepeatable rather than merely fixed.
PRODUCERS: dict[str, str] = {
    "CadreMember": "member",
    "CadreUnit": "unit",
    "CadreGate": "gate",
}


def _manifest() -> dict[str, Any]:
    """The shipped manifest, parsed, with its machine placeholders neutralised.

    The placeholders are not valid TOML values in themselves and they are
    irrelevant here — this function only ever reads the ingest declarations —
    so they are swapped for harmless strings rather than rendered for real.
    """
    from firm.services import base_extension

    raw = base_extension.manifest_source().read_text(encoding="utf-8")
    raw = raw.replace(base_extension.PLACEHOLDER, "/tmp/framework")
    raw = raw.replace(base_extension.HANDLER_PLACEHOLDER,
                      "/tmp/framework/bin/cadre")
    return tomllib.loads(raw)


def state_dir() -> str:
    """The directory base resolves an ingest ``file`` against, per the manifest."""
    return str(_manifest()["extension"]["state_dir"]).strip("/")


def declared_exports() -> list[dict[str, str]]:
    """Every ``[[hooks.session_start.ingest]]`` block the manifest declares.

    Each item carries at least ``file`` and ``entity``. Read from the manifest
    on every call rather than cached: this is cheap, and a cache is one more
    thing that can hold a stale answer after somebody edits the file.
    """
    ingest = _manifest().get("hooks", {}).get("session_start", {}).get("ingest", [])
    return [dict(block) for block in ingest
            if block.get("file") and block.get("entity")]


def _write_atomic(target: Path, payload: str) -> None:
    """Replace *target* with *payload* in one indivisible step.

    The temp file is created in the SAME directory, because ``os.replace`` is
    only atomic within a filesystem and a temp directory can be on another one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                        prefix=".cadre-export-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def export(workspace: Path | str, firm_id: str | None = None, *,
           conn: Any = None) -> dict[str, Any]:
    """Write every file the manifest declares, from the firm database.

    **Never raises.** This is called from ``founding.commit`` and from the end of
    a pulse run, where an export failure must degrade the firm rather than take
    down the caller — the same contract ``base_domain.sync`` and
    ``base_extension.install`` keep. The CLI verb is the one caller that turns a
    false ``ok`` into a non-zero exit, because a person who runs it by hand and
    gets 0 over a failed export has been told the opposite of the truth.

    Pass ``conn`` to export inside a transaction the caller already owns;
    otherwise one is opened and closed here.
    """
    from firm.core import repo
    from firm.core.db import db_connection, get_db_path, resolve_firm_id

    root = Path(workspace)
    result: dict[str, Any] = {"ok": False, "written": [], "skipped": [],
                              "reason": ""}

    try:
        declared = declared_exports()
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        result["reason"] = f"the shipped manifest could not be read: {exc}"
        return result
    if not declared:
        # Not an error and not a success. The manifest asking for nothing is a
        # real state, and reporting it as a clean export would hide the day
        # somebody deletes the ingest blocks.
        result["reason"] = "the manifest declares no ingest files, so nothing was written"
        return result

    if not get_db_path(root).exists():
        result["reason"] = f"no firm database at {get_db_path(root)}"
        return result

    # ---- REFUSE BEFORE WRITING, never clamp ----------------------------
    #
    # The target is `workspace / state_dir / file` and BOTH halves come from
    # the manifest. Measured 2026-09-12 on this very module before this check
    # existed: a manifest declaring `file = "../../escaped.json"` wrote a real
    # 509-byte file one level above the firm's workspace. With the manifest
    # Cadre ships that cannot happen, but "cannot happen with today's input" is
    # not a property of the code, and this function is reached from a founding
    # and from every pulse.
    #
    # REFUSE rather than clamp. Silently rewriting the path to something inside
    # the workspace would hand the manifest a file other than the one it asked
    # for, and base would then ingest a path nobody declared — the same family
    # as a row reporting more than it measured.
    #
    # And refuse BEFORE the first write, not after. That is the whole lesson of
    # #75: `install()`'s read-back detected the operator's tier being written
    # and could not prevent it, because the refusal landed after the subprocess.
    # A guard that runs second is a detector.
    ws_root = root.resolve()
    planned: list[tuple[dict[str, str], Path]] = []
    escapes: list[str] = []
    for block in declared:
        target = (root / state_dir() / str(block["file"])).resolve()
        if target == ws_root or ws_root not in target.parents:
            escapes.append(f"{block['file']} -> {target}")
        planned.append((block, target))
    if escapes:
        result["reason"] = (
            "refusing to export: the manifest declares a path outside the "
            f"firm's workspace {ws_root} — " + "; ".join(escapes)
            + ". Nothing was written.")
        return result

    def _do(connection: Any) -> None:
        fid = resolve_firm_id(connection, firm_id)
        for block, target in planned:
            entity = str(block["entity"])
            table = PRODUCERS.get(entity)
            if table is None:
                # Named in the manifest, produced by nothing. Say which, rather
                # than writing an empty array and calling the export complete —
                # an empty file and a missing producer look identical in the
                # graph and need different fixes.
                result["skipped"].append(
                    f"{block['file']}: the manifest declares entity {entity!r} "
                    "and this module has no producer for it")
                continue
            rows = repo.find(connection, table, firm_id=fid)
            _write_atomic(target, json.dumps(rows, indent=2, default=str) + "\n")
            result["written"].append(str(target))

    try:
        if conn is not None:
            _do(conn)
        else:
            with db_connection(root) as opened:
                _do(opened)
    except (OSError, ValueError) as exc:
        result["reason"] = f"the export did not complete: {exc}"
        return result
    except Exception as exc:                      # noqa: BLE001 - never raises
        result["reason"] = f"the export did not complete: {exc}"
        return result

    if result["skipped"]:
        result["reason"] = ("some declared exports have no producer: "
                            + "; ".join(result["skipped"]))
        return result
    result["ok"] = True
    result["reason"] = (f"wrote {len(result['written'])} export"
                        f"{'' if len(result['written']) == 1 else 's'} "
                        f"to {root / state_dir() / 'base-export'}"
                        if result["written"] else "nothing to write")
    return result


def run_export(workspace: Path | str | None = None, *,
               firm_id: str | None = None) -> int:
    """``firm export base``. Writes the firm's state for base to ingest.

    **This reads ``ok`` and exits non-zero when it is false.** The library call
    above never raises, which is right for the two internal call sites where an
    export failure must not take down a founding or a pulse. It is wrong here.
    A person who runs this by hand, sees exit 0, and believes the graph now
    holds their roster has been told the opposite of the truth — which is issue
    #5's defect, and #62's, and the shape this whole lane keeps finding.
    """
    import sys

    root = Path(workspace) if workspace else Path.cwd()
    result = export(root, firm_id)
    if result.get("ok"):
        print(result.get("reason") or "exported")
        for path in result.get("written", []):
            print(f"  {path}")
        return 0
    print(f"Error: {result.get('reason', 'unknown')}", file=sys.stderr)
    return 1
