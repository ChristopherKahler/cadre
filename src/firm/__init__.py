"""Cadre — Coordinated Agent Deployment Runtime Engine."""

from firm._build_info import BASE_VERSION

__framework_name__ = "Cadre"
__acronym__ = "Coordinated Agent Deployment Runtime Engine"
__internal_package__ = "firm"
__base_version__ = BASE_VERSION


def __getattr__(name: str) -> str:
    """``firm.__version__``, resolved each time it is asked for, never at import.

    What this build reports. NOT a frozen literal: it carries the commit, so two
    builds from two commits are two different versions. That is what stops
    `pip install` of a new wheel into an existing environment from deciding the
    requirement is already satisfied and doing nothing at all. Issue #120.

    Reading the commit from a checkout runs git, and every firm process imports
    this package: the timer's pulse, every Member's CLI call. Resolving it at
    import cost each of them four git children, one of which rewrote the
    checkout's index (#120 G2 re-grade N3).
    """
    if name == "__version__":
        from firm import _build_info
        return _build_info.version_string()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
