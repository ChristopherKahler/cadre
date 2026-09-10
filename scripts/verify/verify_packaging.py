"""Prove the packaging hunk: base_ext ships, dependencies untouched, wheel carries it."""
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path("/home/chriskahler/dev/cadre-wt-extension")
d = tomllib.load(open(ROOT / "pyproject.toml", "rb"))
pd = d["tool"]["setuptools"]["package-data"]["firm"]
print("package-data entries:", len(pd))
for e in pd:
    print("   ", e)
print("dependencies:", d["project"]["dependencies"])

checks = {
    "base_ext listed": "base_ext/*.toml" in pd,
    "five entries": len(pd) == 5,
    "mcp bound unchanged": d["project"]["dependencies"][0] == "mcp>=1.0",
    "cryptography unchanged": any("cryptography" in x for x in d["project"]["dependencies"]),
    "no dependency added": len(d["project"]["dependencies"]) == 2,
}

# The real proof: build a wheel and look inside it.
out = ROOT / "dist-godwit-check"
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
        checks["wheel carries the manifest"] = "firm/base_ext/cadre.toml" in names
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
