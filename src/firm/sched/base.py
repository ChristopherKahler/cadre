"""The scheduler interface every backend answers, and shared helpers.

The contract, consumer by consumer:

  - ``firm heartbeat`` needs a per-firm TIMER: fire ``argv`` every interval.
  - the rails need a SERVICE: keep ``argv`` running, restart on failure.
  - the hub's Board-initiated pulse needs SPAWN_DETACHED: fire-and-forget a
    process that survives the HTTP request (and ideally the hub itself).
  - ``firm doctor`` needs LIST/STATUS/CLEAR_FAILED to spot ghosts.

``status()`` reports only what the platform can honestly answer — a backend
that cannot know the next fire time omits the key rather than inventing one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Protocol

_INTERVAL_RE = re.compile(r"^(\d+)(s|m|min|h|d)$")

_UNIT_SECONDS = {"s": 1, "m": 60, "min": 60, "h": 3600, "d": 86400}


def interval_to_seconds(interval: str) -> int:
    """``30m`` → 1800. Raises ValueError on anything else."""
    m = _INTERVAL_RE.match(interval.strip())
    if not m:
        raise ValueError(
            f"invalid interval {interval!r} — use <number><unit> with unit "
            "one of s/m/min/h/d, e.g. 30m or 1h"
        )
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


def run_cmd(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a scheduler CLI (systemctl/launchctl/schtasks); (rc, output).
    Never raises — an absent binary reports as rc 1 with the reason."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return 1, f"{argv[0]} unavailable: {exc}"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


class SchedulerError(RuntimeError):
    """A scheduler operation failed — message carries the CLI's own words."""


class Scheduler(Protocol):
    """What the autonomy layer may ask of the host OS."""

    name: str

    def available(self) -> tuple[bool, str]:
        """(usable, why-not). Consumers surface why-not verbatim."""
        ...

    def install_timer(
        self, stem: str, *, description: str, workdir: Path,
        env: dict[str, str], argv: list[str], interval: str,
    ) -> dict[str, Any]:
        """Fire ``argv`` every ``interval``. Idempotent per stem."""
        ...

    def install_service(
        self, stem: str, *, description: str, workdir: Path,
        env: dict[str, str], argv: list[str],
    ) -> dict[str, Any]:
        """Keep ``argv`` running (restart on failure where the platform can)."""
        ...

    def remove(self, stem: str) -> dict[str, Any]:
        """Stop + uninstall a timer or service. Idempotent."""
        ...

    def status(self, stem: str) -> dict[str, Any]:
        """{installed, state, failed, workdir?, next_fire?, last_fire?} —
        keys the platform cannot answer are absent, never guessed."""
        ...

    def list_installed(self, prefix: str) -> list[str]:
        """Stems of everything this backend installed under ``prefix``."""
        ...

    def clear_failed(self, stem: str) -> None:
        """Clear failure residue (systemd ghosts). No-op elsewhere."""
        ...

    def restart(self, stem: str) -> tuple[bool, str]:
        """Bounce an installed service. (ok, why-not)."""
        ...

    def spawn_detached(
        self, argv: list[str], *, workdir: Path, env: dict[str, str],
        unit: str | None = None,
    ) -> dict[str, Any]:
        """Fire-and-forget ``argv``; must survive the calling process's
        request thread. ``env`` is ADDITIVE over the current process env —
        an explicit environment, never ambient-only inheritance."""
        ...
