"""The charter template every firm inherits must not privilege one host.

A firm lives on exactly one host and everything touching its state runs there.
That is the real invariant. Until 2026-09-10 the shipped §0 preface said
something narrower and wrong: run ``uname -s``, and on anything but Linux wrap
every shell command in ``wsl.exe -d Ubuntu``. A Windows-hosted firm chartered
from that template is instructed to leave Windows to reach its own database.

The instruction was accurate when written, because Cadre ran only on WSL. It
stopped being accurate when ``firm.sched`` grew launchd and Windows Task
Scheduler backends (``sched/winsched.py``, on disk since 2026-07-14) and the
``.firm/pulse.lock`` flock was replaced by the DB-row lock in
``pulse/dblock.py``. Nothing failed loudly when it went stale; it just kept
shipping in every new firm's charter.

Two arms, because an absence check alone is worthless:

  * NEGATIVE — the blanket-wrapper instruction is gone.
  * POSITIVE — the host-locality law is present.

Without the positive arm, deleting the whole §0 preface would pass. Without the
negative arm, re-adding the old line would pass. And a CONTROL runs both arms
against the pre-patch text, requiring the negative arm to fire on it — a
checker that cannot fail on the known-bad input is not a checker.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import firm

TEMPLATE = Path(firm.__file__).parent / "templates" / "boardroom" / "boardroom.md"

# The exact §0 line as it shipped before 2026-09-10. Kept verbatim so the
# control exercises the real defect rather than a paraphrase of it.
PRE_PATCH_LINE = (
    "1. Run `uname -s`. `Linux` → run commands directly. Otherwise wrap "
    "every shell command: `wsl.exe -d Ubuntu -e bash -lc '<command>'`."
)

# A blanket wrapper is "anything but Linux gets tunnelled", with no reference
# to where the firm actually lives. Matching the whole clause rather than the
# bare string "wsl.exe" matters: naming wsl.exe is CORRECT when it is how you
# reach a WSL-hosted firm, and the patched template does name it that way.
BLANKET_WRAPPER_MARKERS = (
    "Otherwise wrap every shell command",
    "never touch it from a Windows-hosted shell",
)

HOST_LOCALITY_MARKERS = (
    "host that owns the firm",
    "across a host boundary",
)


def _blanket_wrapper_hits(text: str) -> list[str]:
    return [m for m in BLANKET_WRAPPER_MARKERS if m in text]


def _host_locality_hits(text: str) -> list[str]:
    return [m for m in HOST_LOCALITY_MARKERS if m in text]


def test_the_template_is_actually_readable() -> None:
    """A checker that reports zero must first prove it can see.

    Without this, a moved or unpackaged template would make every assertion
    below pass against an empty string.
    """
    assert TEMPLATE.is_file(), f"charter template not found at {TEMPLATE}"
    text = TEMPLATE.read_text(encoding="utf-8")
    assert len(text) > 2000, (
        f"charter template at {TEMPLATE} is only {len(text)} bytes; the "
        "assertions below would be checking almost nothing"
    )
    assert "Step 0" in text, (
        "the template no longer has a Step 0 section, so the §0 preface "
        "this test guards may have moved somewhere it is not being checked"
    )


def test_control_the_checker_fires_on_the_pre_patch_text() -> None:
    """CONTROL: both arms, run against the text known to contain the defect."""
    assert _blanket_wrapper_hits(PRE_PATCH_LINE), (
        "the negative arm did not fire on the exact line that shipped the "
        "defect — it cannot detect a regression either"
    )
    assert not _host_locality_hits(PRE_PATCH_LINE), (
        "the positive arm matched the pre-patch line, so it is not specific "
        "to the new law and would pass on the old text"
    )


def test_the_preface_does_not_blanket_wrap_every_command_for_one_host() -> None:
    """NEGATIVE arm."""
    text = TEMPLATE.read_text(encoding="utf-8")
    hits = _blanket_wrapper_hits(text)
    assert not hits, (
        "the charter template tells every firm to tunnel to another host "
        f"regardless of where it lives: {hits}. Cadre resolves systemd, "
        "launchd and Windows Task Scheduler from sys.platform, so no host is "
        "the privileged one. State the locality law instead."
    )


def test_the_preface_states_the_host_locality_law() -> None:
    """POSITIVE arm — without this, deleting §0 entirely would pass."""
    text = TEMPLATE.read_text(encoding="utf-8")
    missing = [m for m in HOST_LOCALITY_MARKERS if m not in text]
    assert not missing, (
        f"the charter template no longer states the host-locality law: "
        f"missing {missing}. A firm lives on one host and everything touching "
        "its state runs there; that rule has to be IN the charter, not merely "
        "absent from it."
    )


@pytest.mark.parametrize("phrase", ["WSL-only", "WSL only"])
def test_no_wsl_only_law_language_survives(phrase: str) -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert phrase not in text, (
        f"the charter template still calls the framework {phrase!r}. It has "
        "three host scheduler backends; the constraint is locality, not WSL."
    )
