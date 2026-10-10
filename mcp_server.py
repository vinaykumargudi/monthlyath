"""Absolute-path entry point for local stdio MCP clients."""

from app.mcp_server import create_mcp_server

if __name__ == "__main__":
    create_mcp_server().run(transport="stdio")
