# Firm State

Runtime state for all `/firm:*` commands. Always loaded by the entry point.

## Resolution Rules

**Workspace path:**
- Check `$FIRM_WORKSPACE` env var first
- Fall back to current working directory
- CLI flag: `--workspace <path>` overrides both
- Resolution: `Path(os.environ.get("FIRM_WORKSPACE", os.getcwd()))`

**Firm ID:**
- Check `$FIRM_ID` env var
- Default: `chrisai`
- CLI flag: `--firm-id <id>` overrides both
- Resolution: `os.environ.get("FIRM_ID", "chrisai")`

**Database path:**
- Always: `{workspace}/.firm/firm.db`
- No override. If the DB doesn't exist, `/firm:init` must be run first.

**Install status:**
- Detected by checking `{workspace}/.firm/firm.db` exists
- If missing, all commands except `/firm:init` should fail with: "Firm not initialized. Run /firm:init first."

## Standard Invocation

All task files invoke the Python backend via subprocess:

```
python -m firm <command> <sub-command> [args] --workspace <path> --firm-id <id>
```

Examples:
```
python -m firm member create --name "Quill" --role "Blog Author" --workspace /home/user/workspace --firm-id chrisai
python -m firm unit checkout UNIT-001 --member MEM-001
python -m firm status
```

## Current Values

| Field | Value |
|-------|-------|
| Workspace | [Detected at runtime] |
| Firm ID | [From $FIRM_ID or "chrisai"] |
| Database | [workspace]/.firm/firm.db |
| Installed | [Detected: .firm/firm.db exists] |
