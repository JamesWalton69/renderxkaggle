"""Run the MCP server over Streamable HTTP (for remote / Render use)."""
import os

from mcp_server.server import mcp

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
