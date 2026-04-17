# Cadre — Naming & Vocabulary

## Public Brand

**Cadre** — *Coordinated Agent Deployment Runtime Engine*

A cadre is a trained, committed core team. That's the exact mental model this framework encodes: a small, structured group of AI Members operating inside an AI Firm, with roles, ownership, and autonomous delegation.

The acronym is secondary. The word is the identity.

## Public vs Internal Names

| Layer | Name | Where it appears |
|---|---|---|
| Distribution | `cadre` | `pip install cadre`, PyPI, pyproject.toml |
| Import package | `firm` | `from firm.services import member` — unchanged |
| Concept | "Cadre" | README, docs, marketing, user-facing errors |
| Instance | "a Firm" | User's specific installation (e.g., "the chrisai firm") |

Divergent distribution vs import name is standard Python practice — see `beautifulsoup4` → `bs4`, `pyyaml` → `yaml`, `pillow` → `PIL`.

Runtime access:
```python
import firm
firm.__framework_name__   # "Cadre"
firm.__acronym__          # "Coordinated Agent Deployment Runtime Engine"
firm.__internal_package__ # "firm"
```

## Terminology Map

| Layer | Term | Meaning |
|---|---|---|
| Framework | Cadre | The public brand + PyPI distribution |
| Instance | Firm | A Cadre installation (e.g., "chrisai firm") |
| Workforce | Members | The AI agents inside a Firm |
| Authority | Board | The human operator(s) — yes/no authority |
| Work | Units | Atomic tasks Members claim via atomic checkout |
| Strategy | Operations → Projects → Goals | Scoping hierarchy |
| Checkpoints | Gates | Board approval points for significant actions |
| Contract | Contract | Runtime adapter — how a Member actually executes (Claude Code, OpenClaw, etc.) |

## When to Say What

- **"Cadre"** — framework, brand, distribution, docs, install instructions. "Cadre is a framework for running an AI Firm."
- **"Firm"** — a specific instance. "The chrisai firm has three Members." "Install Cadre to run your own Firm."
- **"Member"** — always capitalized when referring to the framework entity (MEM-001, Quill).
- **"Board"** — always capitalized. The human decision authority.

## Usage Examples

- "Install Cadre: `pip install cadre`"
- "Cadre orchestrates a Firm of AI Members."
- "Your Firm's Board decides which hire proposals get approved."
- "A Member checks out a Unit, runs it under their Contract, and ships output."

## Out of Scope

This doc does not cover:
- Internal Python API naming — those are documented in module docstrings
- Skill command names (`/quill:run`, `/sterling:queue`) — those are per-Member and owned by skill authors
- Migration or class names — internal concerns, stable vocabulary

---
*Name locked: 2026-04-17 (Phase 8, plan 08-01)*
