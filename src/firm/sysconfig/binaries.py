"""What kind of executable is this file, and can this host honour it.

ONE home for the magic-byte tables, deliberately. Before this module there were
two copies in this repo and they disagreed about what they covered:

* ``firm/pulse/spawn.py`` carried ``_ELF`` and eight Mach-O magics, for deciding
  whether ``claude`` can be exec'd.
* ``scripts/acceptance-e2e.py`` carried ``binary_kind()``, for deciding whether
  the harness may run a section against the resolved ``base``.

Neither was reachable from ``firm.services.base_extension``, which is the call
site that actually writes the operator's tier. A guard in one script does not
protect a call site in another -- so the third caller gets the shared module
rather than a third copy, and the other two collapse into it as their lanes
land.

WHY MAGIC BYTES AND NEVER A FILENAME: the Windows ``base`` sits at
``/mnt/c/Users/<user>/.local/bin/base`` with no ``.exe`` suffix. A filename test
calls it POSIX, which is wrong in the exact case that caused the 2026-09-11
incident. Four bytes off the front cannot be fooled that way.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Windows PE. Every PE starts "MZ" -- the DOS stub header.
_PE = b"MZ"
#: Linux ELF.
_ELF = b"\x7fELF"
#: Mach-O, all eight spellings. Enumerating only the 64-bit little-endian one
#: is how a guard fails open on half the Macs it meets.
_MACHO: tuple[bytes, ...] = (
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",   # 32-bit, BE / LE
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",   # 64-bit, BE / LE
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",   # universal ("fat"), BE / LE
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",   # universal 64-bit, BE / LE
)

ABSENT = "absent"
UNREADABLE = "unreadable"
UNKNOWN = "unknown"


def image_format(path: str | Path | None) -> str:
    """Classify an executable by its first four bytes.

    Returns ``"pe"``, ``"elf"``, ``"macho"``, or one of ``ABSENT``,
    ``UNREADABLE``, ``UNKNOWN``. The three non-format answers are kept distinct
    because they need different sentences in front of an operator: nothing was
    resolved, something was resolved but could not be read, and something was
    read but is not an image format we know.
    """
    if not path:
        return ABSENT
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError:
        return UNREADABLE
    if magic.startswith(_PE):
        return "pe"
    if magic == _ELF:
        return "elf"
    if magic in _MACHO:
        return "macho"
    return UNKNOWN


def native_image_format() -> str:
    """The image format THIS host's kernel can exec.

    Read at call time rather than import time, so the platform branch stays
    testable from any host.
    """
    if sys.platform == "win32":
        return "pe"
    if sys.platform == "darwin":
        return "macho"
    return "elf"


def base_can_honour_tier(base_path: str | Path | None,
                         expected_tier: str | Path) -> tuple[bool, str]:
    """May this ``base`` be run against the tier we expect it to write?

    ``(True, "")`` when it may. ``(False, reason)`` when it may not, where
    ``reason`` is a sentence an operator can act on.

    A base built for another platform still RUNS here -- WSL interop executes a
    Windows binary quite happily -- and it IGNORES a POSIX ``BASE_HOME``. That
    is not a theory: on 2026-09-11 a POSIX ``BASE_HOME`` handed to a Windows
    base did not redirect the write and did not trip base's own isolation panic
    either, and the operator's real Windows tier gained a rendered manifest
    while every assertion downstream still read clean. So the only safe answer
    for a base whose format is not this host's is to refuse before running it.

    UNKNOWN AND UNREADABLE BOTH REFUSE. Admitting an unidentified binary is
    fail-open, and fail-open here writes the operator's own tier. That is the
    same ruling as "an unidentified base is refused rather than admitted",
    applied one level down from the acceptance harness to the call site that
    does the writing.

    THE WORDING OF EVERY REASON BELOW IS LOAD-BEARING. ``run_install`` returns
    exit code 0 for any reason containing the substring "not installed" --
    that is how a host without base stays a skip rather than a failure. A
    refusal that happened to contain those two words would be reported through
    a success exit code, which is a silent success and the very fault family
    this guard exists to remove. None of these sentences may contain them.
    """
    kind = image_format(base_path)
    native = native_image_format()

    if kind == ABSENT:
        # Callers handle absence before reaching here; if one does not, say so
        # plainly rather than pretending a missing path is a foreign binary.
        return False, ("no base binary was resolved, so there is nothing to "
                       "run and nothing was run")

    if kind == UNREADABLE:
        return False, (
            f"the resolved base at {base_path} could not be read, so its image "
            f"format could not be identified. An unidentified base is refused "
            f"rather than admitted: it may ignore BASE_HOME and write the "
            f"operator's own tier instead of {expected_tier}. Nothing was run.")

    if kind == UNKNOWN:
        return False, (
            f"the resolved base at {base_path} is not an image format this "
            f"check recognises, so it cannot be shown to honour BASE_HOME. An "
            f"unidentified base is refused rather than admitted, because it "
            f"may write the operator's own tier instead of {expected_tier}. "
            f"Nothing was run.")

    if kind != native:
        return False, (
            f"the resolved base at {base_path} is a {kind.upper()} binary and "
            f"this host runs {native.upper()}. A base built for another "
            f"platform still executes here and ignores BASE_HOME, so it would "
            f"write the operator's own tier instead of {expected_tier}. "
            f"Refused before anything ran. Put a base built for "
            f"{native.upper()} first on PATH, or take it off PATH and let this "
            f"step be skipped.")

    return True, ""
