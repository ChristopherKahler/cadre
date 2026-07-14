"""System config — the dashboard's platform-aware firm-workspace editor.

Mirrors the Contract runtime pattern: a registry of platform adapters,
each declaring the config surfaces (files, inventories) its platform
keeps in a firm folder. Claude Code is the implemented adapter; new
platforms register a new adapter, the routes and UI stay unchanged.
"""

from firm.sysconfig.platforms import PlatformAdapter, Surface, detect_platform

__all__ = ["PlatformAdapter", "Surface", "detect_platform"]
