"""Hook implementations for the firm framework.

Read-only SessionStart renderer (``session_pulse``), plus callable wrappers
for unit-completion and run-record.
"""

from firm.hooks.run_record import on_run_end
from firm.hooks.unit_completion import on_unit_done

__all__ = ["on_run_end", "on_unit_done"]
