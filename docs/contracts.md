# Writing a Contract Runtime

Cadre is runtime-agnostic by design. The reference implementation targets Claude Code, but any agent runtime that can execute a prompt and return output can be plugged in through the `ContractRuntime` Protocol.

> **Terminology.** Cadre calls these **Contract runtimes**, not *adapters*. A `Contract` is a first-class entity on the Firm; a runtime is the code that executes it. "Adapter" is Paperclip vocabulary — Cadre keeps its own.

## Why Contract Runtimes Exist

A `Member` owns identity (role, skills, reports-to). A `Contract` owns runtime (which system actually executes the Member). Splitting those two concerns means you can swap Claude Code for OpenClaw, Codex, or any future runtime without rewriting your Firm.

## The ContractRuntime Protocol

Defined in `src/firm/contracts/interface.py`. Three methods, structural typing (Python Protocol). Your class does not inherit from `ContractRuntime`; the type system recognizes it as a valid implementation when the signatures match.

### `invoke(conn, contract, member, unit, *, cwd) -> InvokeResult`

Start a single execution. Build the prompt from `contract.skill_loadout` plus the `unit` context, spawn your runtime, capture output, return an `InvokeResult`.

Arguments:
- `conn` — SQLite connection for any DB reads your prompt assembly needs
- `contract` — the Contract row dict (`runtime_type`, `skill_loadout`, `runtime_config`, etc.)
- `member` — the Member row dict (role, description, reports_to, etc.)
- `unit` — the Unit the Member is executing against
- `cwd` — working directory for the runtime process

Returns: `InvokeResult` with:
- `handle` — opaque `RunHandle` for `status` / `cancel` lookups
- `stdout`, `stderr`, `returncode`
- `timed_out: bool`
- `prompt_snapshot` — the full prompt actually sent, for audit

### `status(handle) -> RunStatus`

Look up the current state of a previously-invoked run. Return one of: `running`, `completed`, `failed`, `timed_out`, `cancelled`.

### `cancel(handle) -> bool`

Abort a running invocation. Return `True` if cancellation succeeded, `False` if the run was already terminal or the handle is unknown.

## Data Types

All defined in `firm.contracts.interface`:

| Type | Purpose |
| :--- | :--- |
| `RunStatus` | Enum of lifecycle states |
| `RunHandle` | Opaque handle: `run_id`, `pid`, `metadata` (runtime-specific) |
| `InvokeResult` | Returned from `invoke`; carries handle + output + snapshot |

## Registering Your Runtime

```python
from firm.contracts.registry import register_runtime
from firm.contracts.your_runtime import YourRuntime

register_runtime("your_runtime_name", YourRuntime())
```

After registration, any `Contract` row with `runtime_type = "your_runtime_name"` dispatches to your runtime. The PULSE runner calls `resolve_runtime(contract)` to look up the right runtime by `runtime_type`.

## Reference Implementation

`src/firm/contracts/claude_code.py` is the canonical example. It spawns `claude --print`, parses stream-JSON, and maps Claude Code's exit codes to the `RunStatus` enum. Read it side-by-side with your stub when implementing a new runtime.

## Stub Templates

Two copy-to-start stubs live in [`templates/contracts/`](../templates/contracts/):

- `openclaw_stub.py` — for OpenClaw
- `codex_stub.py` — for OpenAI Codex

Copy the stub into `src/firm/contracts/`, implement the three methods, register it, and your Members can now run on that runtime.

## Testing

Fast path: unit-test `invoke` with a stubbed process (no real runtime call). Assert the returned `InvokeResult` is well-formed. Integration-test against the real runtime in a small fixture workspace. The existing PULSE integration tests in `tests/test_pulse_runner.py` are a good reference shape.

## Conformance Check (Optional)

Uncomment the final line in the stub files to enforce Protocol conformance at import time:

```python
_: ContractRuntime = YourRuntime()
```

If your class is missing a method or has the wrong signature, Python will catch it when the module imports.
