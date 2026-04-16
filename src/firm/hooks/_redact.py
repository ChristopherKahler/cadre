"""Credential redaction utility.

Strips keys matching common credential patterns from dicts and strings
before immutable data is committed to the database. Records are immutable
-- accidental secret-logging can't be undone.
"""

from __future__ import annotations

import re
from typing import Any

_KEY_PATTERN = re.compile(r"(?i)(token|key|secret|password)")

_STRING_PATTERN = re.compile(
    r"(?i)([\w-]*(?:token|key|secret|password)[\w-]*)(\s*[:=]\s*)(\S+)",
)


def redact(value: Any) -> Any:
    """Return a deep copy of *value* with credential-shaped data replaced.

    - Dicts: keys matching ``/token|key|secret|password/i`` get their values
      replaced with ``"[REDACTED]"``.  Nested dicts/lists are walked.
    - Strings: substrings matching ``key=value`` or ``key: value`` where the
      key contains a credential word get the value replaced.
    - Lists: each element is redacted recursively.
    - Other types: returned as-is.

    The original input is **never** mutated.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _KEY_PATTERN.search(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _STRING_PATTERN.sub(r"\1\2[REDACTED]", value)
    return value
