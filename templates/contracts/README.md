# Contract Runtime Templates

Copy-to-start stubs for implementing new Cadre Contract runtimes. Each file is a valid Python module that declares the `ContractRuntime` Protocol methods with `NotImplementedError` bodies. Fill in the three methods, drop the file into `src/firm/contracts/`, and register it with the runtime resolver.

> Cadre calls these **Contract runtimes**, not adapters. "Adapter" is Paperclip vocabulary. We keep our own.

## Available Stubs

| File | Target Runtime |
| :--- | :--- |
| `openclaw_stub.py` | OpenClaw (Anthropic's public successor to Claude Code) |
| `codex_stub.py` | OpenAI Codex / Codex CLI |

## Usage

```bash
cp templates/contracts/openclaw_stub.py src/firm/contracts/openclaw.py
# edit src/firm/contracts/openclaw.py: implement invoke, status, cancel
# then register:
```

```python
from firm.contracts.registry import register_runtime
from firm.contracts.openclaw import OpenClawRuntime

register_runtime("openclaw", OpenClawRuntime())
```

After registration, any Contract with `runtime_type = "openclaw"` will dispatch to your runtime.

## Authoring Guide

See [docs/contracts.md](../../docs/contracts.md) for the full Protocol, data types, and reference implementation walkthrough.

## Reference Implementation

`src/firm/contracts/claude_code.py` ships as the canonical Contract runtime. Read it alongside these stubs when you implement your own.
