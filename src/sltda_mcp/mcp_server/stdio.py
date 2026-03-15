"""
stdio transport entry point — for Claude Desktop integration.
Run with: python -m sltda_mcp.mcp_server.stdio
"""

from sltda_mcp.mcp_server.main import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
