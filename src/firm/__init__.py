"""Cadre — Coordinated Agent Deployment Runtime Engine."""

from firm._build_info import BASE_VERSION, version_string

#: What this build reports. NOT a frozen literal: it carries the commit, so
#: two builds from two commits are two different versions. That is what stops
#: `pip install` of a new wheel into an existing environment from deciding the
#: requirement is already satisfied and doing nothing at all. Issue #120.
__version__ = version_string()

__framework_name__ = "Cadre"
__acronym__ = "Coordinated Agent Deployment Runtime Engine"
__internal_package__ = "firm"
__base_version__ = BASE_VERSION
