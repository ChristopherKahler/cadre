"""Credential redaction utility.

Strips keys matching common credential patterns from dicts and strings
before immutable data is committed to the database. Records are immutable
-- accidental secret-logging can't be undone.
"""

from __future__ import annotations

import re
from typing import Any

# Dict-key words that mark a value as a credential. "authorization" is spelled
# in full deliberately — a bare "auth" would also match author_id / author_type,
# which the Records layer carries everywhere (a redactor that eats provenance is
# its own bug). "bearer"/"credential"/"apikey" are safe, non-colliding adds.
_KEY_PATTERN = re.compile(
    r"(?i)(token|key|secret|password|authorization|bearer|credential|apikey)"
)

_STRING_PATTERN = re.compile(
    r"(?i)([\w-]*(?:token|key|secret|password)[\w-]*)(\s*[:=]\s*)(\S+)",
)

# `Authorization: Bearer <tok>` (audit A9) and any standalone `Bearer <tok>` —
# the header key carries no credential word, so _STRING_PATTERN alone misses it.
# Redacts the token, keeps the "Bearer " marker so the shape stays legible.
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)(\S+)")


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
        redacted = _STRING_PATTERN.sub(r"\1\2[REDACTED]", value)
        return _BEARER_PATTERN.sub(r"\1[REDACTED]", redacted)
    return value
