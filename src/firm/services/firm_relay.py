"""The firm's own message store, and a steer that cannot reach says so.

WHY THIS MODULE EXISTS. #117 gives every firm its own BASE_HOME so nothing
outside the firm can write into its graph. base keeps the relay store in that
same tier, so the messaging moves with the graph: a Member spawned in a firm
registers its title in the FIRM's store, and a session standing anywhere else
looks in a different one. That is correct -- a firm's internal traffic belongs
to the firm -- but it has a failure mode that must never be silent.

THE FAILURE THAT MUST BE LOUD. The steer path used to return None on every
failure and fall back to queueing, so "the Member is not registered", "base is
not installed" and "the store has nobody in it" were one answer. With per-firm
stores there is a fourth cause and it looks exactly like the others: the sender
and the receiver are in DIFFERENT STORES. A silent fallback there means the
Board types a steer, sees nothing wrong, and the Member never hears it. So
every function here returns a reason, and the reason names the store PATH it
looked in, the title it wanted, and the titles that were actually there.

WHAT CADRE OFFERS INSTEAD OF A BARE `base relay`. `cadre relay ping|task|
sessions` resolves the firm (explicitly, or by walking up from the current
directory), points base at that firm's tier, and drives base's own relay there.
A person steering a firm does not have to know where the store is, or set
anything in their environment, and `base cadre relay ...` works from anywhere.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from firm.core.proc import run_utf8
from firm.services.graph_isolation import firm_base_home


def store_path(workspace: Path | str) -> Path:
    """Where this firm's messages live. Named in every failure this module has.

    Measured layout, base 0.15.2: with BASE_HOME set, base makes its relay
    inbox at `<BASE_HOME>/.base-gbl/.base/relay-inbox`.
    """
    return firm_base_home(workspace) / ".base-gbl" / ".base" / "relay-inbox"


def resolve_firm(start: Path | str | None = None) -> Path | None:
    """The firm you are standing in, or None.

    Walks up from *start* (default: the current directory) looking for
    `.firm/firm.db`. A firm is a directory, not an environment variable, so a
    person who has cd'd into one should not have to name it.
    """
    here = Path(start) if start else Path.cwd()
    here = here.resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".firm" / "firm.db").exists():
            return candidate
    return None


def explicit_firm(workspace: Path | str) -> tuple[Path | None, str]:
    """A firm someone NAMED, or None and the sentence that says why it is not one.

    Refused rather than guessed about. A person who names a directory has said
    which firm they mean, so a directory with no `.firm/firm.db` is a mistake to
    report, not a firm to create. Handing it to base as a workspace creates that
    directory's tier on disk (`graph_isolation.ensure_tier`), so a typo would
    leave a `.firm/` behind in whatever it named. Every verb that takes an
    explicit firm calls this one helper, so no two verbs can disagree about what
    counts as a firm.
    """
    path = Path(workspace).expanduser()
    if not (path / ".firm" / "firm.db").exists():
        return None, f"{path} is not a firm — no .firm/firm.db there."
    return path, ""


def _base() -> tuple[str | None, str]:
    from firm.sysconfig.service import base_absence_reason, which_base

    found = which_base()
    if found:
        return found, ""
    return None, base_absence_reason()


def _run(workspace: Path, args: list[str], timeout: int = 30):
    from firm.services.base_domain import _base_env

    binary, absent = _base()
    if not binary:
        raise FileNotFoundError(absent)
    return run_utf8([binary, "relay", *args], capture_output=True,
                    timeout=timeout, cwd=str(workspace),
                    env=_base_env(workspace), stdin=subprocess.DEVNULL)


def sessions(workspace: Path | str) -> dict[str, Any]:
    """Who is registered in THIS firm's store.

    ``ok`` False with a reason whenever the listing could not be read. An
    unreadable store is not an empty store, and reporting "nobody is here" over
    a failed read is how a steer gets blamed on the Member.
    """
    workspace = Path(workspace)
    out: dict[str, Any] = {"ok": False, "store": str(store_path(workspace)),
                           "titles": {}, "reason": ""}
    try:
        listed = _run(workspace, ["sessions"])
    except FileNotFoundError as exc:
        out["reason"] = str(exc)
        return out
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["reason"] = f"base relay sessions did not run: {exc}"
        return out
    if listed.returncode != 0:
        said = (listed.stderr or listed.stdout or "").strip().splitlines()
        out["reason"] = (f"base relay sessions exited {listed.returncode}: "
                         + (said[-1] if said else "and said nothing"))
        return out
    for line in (listed.stdout or "").splitlines():
        if "session:" not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        out["titles"][parts[0]] = {
            "live": "[live" in line,
            "line": line.strip(),
        }
    out["ok"] = True
    return out


def _unreachable(workspace: Path, to: str, known: dict[str, Any]) -> str:
    """The loud sentence. It names the store, the title, and what IS there."""
    others = ", ".join(sorted(known)) or "nobody is registered there"
    return (
        f"no session titled {to!r} in this firm's message store.\n"
        f"  store looked in : {store_path(workspace)}\n"
        f"  titles there    : {others}\n"
        "  This firm keeps its own message store, the same way it keeps its own "
        "graph, so a session that started outside the firm registers somewhere "
        "else and cannot be reached from here. Start it through Cadre, or steer "
        "it with cadre relay from inside this firm.")


def _send(workspace: Path | str, verb: str, to: str, args: list[str],
          *, require_live: bool) -> dict[str, Any]:
    workspace = Path(workspace)
    out: dict[str, Any] = {"ok": False, "store": str(store_path(workspace)),
                           "to": to, "reason": ""}
    known = sessions(workspace)
    if not known["ok"]:
        out["reason"] = (f"could not read this firm's message store, so "
                         f"whether {to!r} is there is unknown: {known['reason']}")
        return out
    titles = known["titles"]
    if to not in titles:
        out["reason"] = _unreachable(workspace, to, titles)
        return out
    if require_live and not titles[to]["live"]:
        out["reason"] = (
            f"{to!r} is registered in {store_path(workspace)} but is not live "
            f"({titles[to]['line']}), so a mid-turn steer has nothing to land "
            "in. Its next session start will not replay this.")
        return out
    try:
        sent = _run(workspace, [verb, *args])
    except FileNotFoundError as exc:
        out["reason"] = str(exc)
        return out
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["reason"] = f"base relay {verb} did not run: {exc}"
        return out
    if sent.returncode != 0:
        said = (sent.stderr or sent.stdout or "").strip().splitlines()
        out["reason"] = (f"base relay {verb} exited {sent.returncode}: "
                         + (said[-1] if said else "and said nothing"))
        return out
    out["ok"] = True
    return out


def ping(workspace: Path | str, *, to: str, message: str,
         from_name: str = "cadre") -> dict[str, Any]:
    """Send a ping inside the firm's own store. Reports why, when it cannot."""
    return _send(workspace, "ping", to,
                 ["--to", to, "--from", from_name, "--msg", message],
                 require_live=False)


def task(workspace: Path | str, *, to: str, summary: str, slug: str,
         from_name: str = "cadre") -> dict[str, Any]:
    """Deliver a steer into a LIVE session in the firm's own store.

    A task rather than a ping on purpose: the receiver clears a task itself,
    while a ping only clears on a reply to the sender, and an unattended sender
    leaves the alert re-firing forever (proven live, 2026-07-13).
    """
    return _send(workspace, "task", to,
                 ["--to", to, "--from", from_name, "--slug", slug,
                  "--summary", summary],
                 require_live=True)
