"""Platform adapters — what "system config" means per agent platform.

An adapter owns the mapping from stable surface keys to workspace-relative
paths. The dashboard only ever speaks surface keys; raw paths never cross
the HTTP boundary, so there is no traversal surface to defend.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any


@dataclasses.dataclass(frozen=True)
class Surface:
    key: str            # stable route key, e.g. "claude-md"
    label: str
    path: str           # workspace-relative, adapter-owned
    kind: str           # "markdown" | "json"
    description: str


class ClaudeCodeAdapter:
    id = "claude_code"
    label = "Claude Code"

    SURFACES: tuple[Surface, ...] = (
        Surface(
            key="claude-md", label="CLAUDE.md", path="CLAUDE.md",
            kind="markdown",
            description="Firm instructions loaded into every session.",
        ),
        Surface(
            key="settings", label="settings.json", path=".claude/settings.json",
            kind="json",
            description="Claude Code settings — hooks, permissions, env.",
        ),
        Surface(
            key="mcp", label=".mcp.json", path=".mcp.json",
            kind="json",
            description="MCP servers available to the firm's sessions.",
        ),
    )

    def detect(self, workspace: Path) -> bool:
        return (
            (workspace / ".claude").is_dir()
            or (workspace / ".mcp.json").is_file()
            or (workspace / "CLAUDE.md").is_file()
        )

    def surfaces(self) -> tuple[Surface, ...]:
        return self.SURFACES

    def surface(self, key: str) -> Surface:
        for s in self.SURFACES:
            if s.key == key:
                return s
        raise ValueError(f"unknown config surface {key!r}")

    def inventory(self, workspace: Path) -> dict[str, Any]:
        """Skills and commands installed in the firm's .claude/."""
        skills = []
        skills_dir = workspace / ".claude" / "skills"
        if skills_dir.is_dir():
            for d in sorted(skills_dir.iterdir()):
                if not d.is_dir():
                    continue
                desc = _skill_description(d / "SKILL.md")
                skills.append({"name": d.name, "description": desc})
        commands = []
        commands_dir = workspace / ".claude" / "commands"
        if commands_dir.is_dir():
            commands = sorted(
                p.stem for p in commands_dir.glob("*.md") if p.is_file()
            )
        return {"skills": skills, "commands": commands}


def _skill_description(skill_md: Path) -> str:
    """First `description:` value from SKILL.md frontmatter, best-effort."""
    if not skill_md.is_file():
        return ""
    try:
        for line in skill_md.read_text(encoding="utf-8").splitlines()[:30]:
            stripped = line.strip()
            if stripped.lower().startswith("description:"):
                return stripped.split(":", 1)[1].strip().strip("\"'")[:200]
    except OSError:
        pass
    return ""


ADAPTERS: tuple[Any, ...] = (ClaudeCodeAdapter(),)


class PlatformAdapter:  # typing alias for callers; adapters are duck-typed
    id: str
    label: str


def detect_platform(workspace: Path):
    """First adapter whose markers match, or None (unknown platform —
    the dashboard shows variables only, no file surfaces)."""
    for adapter in ADAPTERS:
        if adapter.detect(workspace):
            return adapter
    return None
