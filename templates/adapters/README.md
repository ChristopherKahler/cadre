# Runtime Adapter Templates

Copy-to-start stubs for implementing new Cadre runtime adapters. Each file is a valid Python module that declares the `ContractRuntime` Protocol methods with `NotImplementedError` bodies. Fill in the three methods, drop the file into `src/firm/contracts/`, and register it with the runtime resolver.

## Available Stubs

| File | Target Runtime |
| :--- | :--- |
| `openclaw_stub.py` | OpenClaw (Anthropic's public successor to Claude Code) |
| `codex_stub.py` | OpenAI Codex / Codex CLI |

## Usage

```bash
cp templates/adapters/openclaw_stub.py src/firm/contracts/openclaw.py
# edit src/firm/contracts/openclaw.py: implement invoke, status, cancel
# then register:
```

```python
from firm.contracts.registry import register_runtime
from firm.contracts.openclaw import OpenClawRuntime

register_runtime("openclaw", OpenClawRuntime())
```

After registration, any Contract with `runtime_type = "openclaw"` will dispatch to your adapter.

## Authoring Guide

See [docs/adapters.md](../../docs/adapters.md) for the full Protocol, data types, and reference implementation walkthrough.

## Reference Implementation

`src/firm/contracts/claude_code.py` ships as the canonical adapter. Read it alongside these stubs when you implement your own.
