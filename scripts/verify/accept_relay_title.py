"""Acceptance leg for DoD 3 — the channel base actually reads.

The unit tests prove what env ``spawn_member_run`` builds. They cannot prove
base HONOURS it, which is a different claim about a different process. This leg
takes the real env the real spawn produces and feeds it to a real
``base hook session-start``, then reads the title back out of base's own
registry.

⚑ INSTRUMENT NOTE, and it is the reason this file has a history. The first
version of arm B was INERT (law 42's boundary axis): it warmed the store using
the SAME env, so the warm-up runs had already bound the pinned title, and
"reclaimed" and "pinned" produced the identical string. Arm B passed for the
wrong reason and could not have failed. The warm-up now models what actually
warms an operator's store — OTHER sessions, with no BASE_RELAY_AS of their own —
so a shared WT_SESSION makes the member reclaim SOMEBODY ELSE'S codename, which
is a different string and therefore a canary that can go red.

Three arms, one variable:
  A  the shipped env                          -> the member's own title
  B  the same env with WT_SESSION put back    -> reclaims the warm session's
                                                 pool codename (the defect)
  C  a title never used anywhere              -> appears nowhere

Everything happens under an isolated BASE_HOME. The operator's real relay
registry is never written.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/chriskahler/dev/cadre-wt-extension/src")

from firm.pulse import spawn as spawn_mod  # noqa: E402

ROOT = Path("/tmp/godwit-accept-relay")
FIRM = "zqtestfirm"
MEMBER = "MEM-001"
WANT = f"{FIRM}-{MEMBER}"
NEVER = "zqnevertitle"
SHARED_WT = "133ce31a-8475-4e49-a2e6-9d3160fa1c9f"

BASE = shutil.which("base") or "/home/chriskahler/.local/bin/base"
print(f"PROVENANCE base={BASE} "
      f"version={subprocess.run([BASE, '--version'], capture_output=True, text=True).stdout.strip()} "
      f"md5={hashlib.md5(Path(BASE).read_bytes()).hexdigest()}")
print(f"PROVENANCE spawn.py md5="
      f"{hashlib.md5(Path(spawn_mod.__file__).read_bytes()).hexdigest()}")
print()


def build_member_env() -> dict:
    """Run the REAL spawn_member_run and capture the REAL env it hands Popen."""
    captured: dict = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            captured["env"] = kwargs.get("env")
            raise OSError("captured")

    real_popen = spawn_mod.subprocess.Popen
    real_resolve = spawn_mod.resolve_claude_bin
    spawn_mod.subprocess.Popen = FakePopen
    spawn_mod.resolve_claude_bin = lambda: ("/bin/echo", "acceptance")
    try:
        spawn_mod.spawn_member_run("work", cwd=str(ROOT / "ws"),
                                   firm_id=FIRM, member_id=MEMBER)
    finally:
        spawn_mod.subprocess.Popen = real_popen
        spawn_mod.resolve_claude_bin = real_resolve
    return dict(captured["env"])


def _hook(env: dict, ws: Path, sid: str) -> str:
    out = subprocess.run(
        [BASE, "hook", "session-start"], cwd=ws, env=env,
        input=json.dumps({"session_id": sid, "transcript_path": "/tmp/n.jsonl",
                          "cwd": str(ws), "hook_event_name": "SessionStart",
                          "source": "startup"}),
        capture_output=True, text=True, timeout=120)
    return out.stdout or ""


def _title_of(text: str) -> str:
    for line in text.splitlines():
        if "RELAY WAKE CONTRACT (" in line:
            return line.split("RELAY WAKE CONTRACT (", 1)[1].split(")", 1)[0]
    return ""


def fire(member_env: dict, label: str, *, wt_session: str | None) -> dict:
    """Warm the store with OTHER sessions, then run the member's own."""
    home = ROOT / f"home-{label}"
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    ws = ROOT / "ws"

    base_env = {"HOME": os.path.expanduser("~"),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "BASE_HOME": str(home)}

    # The warm-up sessions are the OPERATOR'S, not the member's: no pinned title
    # of their own. This is what makes arm B able to fail — the thing the member
    # would reclaim is a different string from the thing it should be pinned to.
    warm_env = dict(base_env)
    if wt_session:
        warm_env["WT_SESSION"] = wt_session
    subprocess.run([BASE, "scaffold", "."], cwd=ws, env=warm_env,
                   capture_output=True, text=True, timeout=120)
    warm_titles = [_title_of(_hook(warm_env, ws, f"operator-{label}-{i}"))
                   for i in (1, 2)]

    child = dict(member_env)
    child.update(base_env)
    if wt_session:
        child["WT_SESSION"] = wt_session
    else:
        child.pop("WT_SESSION", None)

    sid = f"member-run-{label}"
    contract = _title_of(_hook(child, ws, sid))

    reg = subprocess.run([BASE, "relay", "sessions"], cwd=ws, env=child,
                         capture_output=True, text=True, timeout=60)
    registry = ""
    for line in (reg.stdout or "").splitlines():
        if f"session:{sid}" in line:
            registry = line.strip().split()[0]
            break
    return {"contract": contract, "registry": registry, "warm": warm_titles}


if ROOT.exists():
    shutil.rmtree(ROOT)
(ROOT / "ws").mkdir(parents=True)

env_a = build_member_env()
print(f"the env spawn_member_run built: BASE_RELAY_AS={env_a.get('BASE_RELAY_AS')!r} "
      f"WT_SESSION={'present' if 'WT_SESSION' in env_a else 'absent'}")
print()

a = fire(env_a, "A", wt_session=None)
b = fire(env_a, "B", wt_session=SHARED_WT)

rows = [
    ("A  shipped env (WT stripped)", a,
     a["contract"] == WANT and a["registry"] == WANT,
     f"pinned to {WANT}"),
    ("B  WT_SESSION put back (canary)", b,
     b["contract"] != WANT and b["contract"] in b["warm"],
     "reclaims an operator codename"),
]

print(f"  {'ARM':32s} {'CONTRACT':22s} {'REGISTRY':22s} {'WARM SESSIONS':22s} VERDICT")
ok = True
for label, r, passed, _ in rows:
    print(f"  {label:32s} {r['contract'] or '<none>':22s} "
          f"{r['registry'] or '<none>':22s} {','.join(r['warm']):22s} "
          f"{'ok' if passed else 'FAIL'}")
    ok = ok and passed

print(f"\n  ARMS VISITED = {len(rows)}")
distinct = {a["contract"], b["contract"]}
print(f"  distinct titles across arms = {len(distinct)} {sorted(distinct)}")
never = sum(1 for _, r, _, _ in rows if NEVER in (r["contract"], r["registry"]))
print(f"  negative control ({NEVER}) hits = {never}")

if len(rows) != 2:
    print("REFUSE: expected 2 arms")
    sys.exit(2)
if never:
    print("REFUSE: the negative control appeared")
    sys.exit(2)
if len(distinct) < 2:
    print("REFUSE: both arms produced the SAME title — the canary is inert and "
          "this run discriminates nothing (law 42, boundary axis)")
    sys.exit(2)
if not ok:
    print("ACCEPTANCE FAILED")
    sys.exit(1)
print("\nACCEPTANCE OK: the shipped env pins the member's own title; putting "
      "WT_SESSION back makes the run reclaim an operator codename instead, so "
      "the strip is what is doing the work.")
