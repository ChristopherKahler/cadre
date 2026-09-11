"""Prove the packaging hunk: base_ext ships, dependencies untouched, wheel carries it."""
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

# Derived, never hard-coded. This read "/home/chriskahler/dev/cadre-wt-extension",
# so running it from any other checkout verified a DIFFERENT tree than the one
# being changed and reported on it as though it were this one.
ROOT = Path(__file__).resolve().parent.parent.parent
d = tomllib.load(open(ROOT / "pyproject.toml", "rb"))
pd = d["tool"]["setuptools"]["package-data"]["firm"]
deps = d["project"]["dependencies"]
dep_mcp = next((x for x in deps if x.startswith("mcp")), "")
print("package-data entries:", len(pd))
for e in pd:
    print("   ", e)
print("dependencies:", d["project"]["dependencies"])

# Globbed against the real directory, not string-matched. A pattern can be
# present in pyproject and match nothing on disk -- which is exactly what
# "base_ext/*.toml" did once the manifest became cadre.toml.template.
import fnmatch
base_ext_dir = ROOT / "src" / "firm" / "base_ext"
shipped = sorted(p.name for p in base_ext_dir.iterdir() if p.is_file())
matched = sorted({n for n in shipped
                  for pat in pd if pat.startswith("base_ext/")
                  and fnmatch.fnmatch(n, pat.split("/", 1)[1])})
print("base_ext on disk:", shipped)
print("base_ext matched by package-data:", matched)

checks = {
    "every base_ext file is matched by a package-data pattern": matched == shipped,
    "base_ext is not empty": bool(shipped),
    "six entries": len(pd) == 6,
    # NOT a frozen string. This read `== "mcp>=1.0"` and went red the day the
    # upper bound was added on purpose -- a guard that fails on a correct change
    # gets edited to whatever the code says, which is how it stops guarding. The
    # floor and the ceiling are the two things that actually matter.
    "mcp floor is 1.0": dep_mcp.startswith("mcp>=1.0"),
    "mcp keeps its upper bound": "<2" in dep_mcp,
    "cryptography unchanged": any("cryptography" in x for x in d["project"]["dependencies"]),
    "no dependency added": len(d["project"]["dependencies"]) == 2,
}

# The real proof: build a wheel and look inside it.
#
# BUILD DIRECTORY CLEARED FIRST, and this is not hygiene. setuptools copies the
# source tree into build/lib and NEVER deletes what has disappeared from source.
# Measured 2026-09-11: after cadre.toml was renamed to cadre.toml.template, a
# rebuild in this tree produced a wheel carrying BOTH -- so `base extension
# install <site-packages>/firm/base_ext/cadre.toml` still resolved, and F6 read
# as fixed in git while shipping unfixed in the artifact. The source tree is not
# the product. The wheel is.
out = ROOT / "dist"
stale = ROOT / "build"
if stale.exists():
    print(f"  clearing {stale} so the wheel reflects source and not history")
    shutil.rmtree(stale, ignore_errors=True)
shutil.rmtree(out, ignore_errors=True)
print("\nbuilding a wheel to see what it actually carries...")
r = subprocess.run(
    [str(ROOT / ".venv/bin/python"), "-m", "pip", "wheel", "--no-deps",
     "-w", str(out), str(ROOT)],
    capture_output=True, text=True, timeout=600)
if r.returncode != 0:
    print("  wheel build failed:", (r.stderr or r.stdout)[-400:])
    checks["wheel carries the manifest"] = False
else:
    wheels = sorted(out.glob("cadre-*.whl"))
    if not wheels:
        print("  no wheel produced")
        checks["wheel carries the manifest"] = False
    else:
        names = zipfile.ZipFile(wheels[-1]).namelist()
        hit = [n for n in names if "base_ext" in n]
        print(f"  wheel: {wheels[-1].name}, {len(names)} members")
        print(f"  base_ext members: {hit}")
        checks["wheel carries the manifest"] = all(
            f"firm/base_ext/{n}" in names for n in shipped)
        # F6, asserted where it actually bites. The refusal IS the absence of
        # this name: base cannot be made to reject the manifest's content
        # (validate and install both return 0 on a handler that points at
        # nothing), but it does reject a file it cannot read. A wheel that
        # carries cadre.toml hands the user back the working hand-install and
        # the dead command that goes with it.
        checks["the wheel ships NO installable cadre.toml"] = (
            "firm/base_ext/cadre.toml" not in names)
        # control: something we know is packaged, and something that must not be
        checks["control - migrations ship"] = any(n.endswith(".sql") for n in names)
        checks["control - tests do not ship"] = not any(n.startswith("tests/") for n in names)

print(f"\nCHECKS VISITED = {len(checks)}")
fails = [k for k, v in checks.items() if not v]
for k, v in checks.items():
    print(f"  {'OK  ' if v else 'FAIL'} {k}")
if fails:
    print(f"FAILED: {fails}")
    sys.exit(1)
print("PACKAGING OK")
