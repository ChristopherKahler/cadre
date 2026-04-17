"""Firm MCP server entry point — FastMCP with stdio transport.

Usage:
    python -m firm.mcp.server

Registers all entity tools from firm.mcp.tools.
DB path resolved from FIRM_CWD env var or cwd.
"""

from firm.mcp.tools import mcp

if __name__ == "__main__":
    mcp.run()
