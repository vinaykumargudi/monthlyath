"""Unauthenticated MCP tools, shared by Streamable HTTP and stdio."""

import asyncio
from datetime import date
import os
from typing import Annotated, Literal
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from app.history import HistoryError, START_DATE
from app.models import MonthlyHistory
from app import service


def transport_security() -> TransportSecuritySettings:
    """Keep Host/Origin validation without requiring credentials."""
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"]
    origins = ["http://localhost", "http://localhost:*", "http://127.0.0.1", "http://127.0.0.1:*", "http://[::1]", "http://[::1]:*"]
    public_url = os.environ.get("MCP_PUBLIC_URL", "").strip()
    if public_url:
        parsed = urlsplit(public_url)
        if (
            parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or "*" in parsed.netloc
        ):
            raise ValueError("MCP_PUBLIC_URL must be an origin such as https://monthlyath.example.com (without /mcp).")
        hosts.append(parsed.netloc)
        origins.append(f"{parsed.scheme}://{parsed.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        "monthlyath",
        instructions=(
            "Download monthly OHLC for Indian stocks from Upstox. An optional start_date defaults to 2000-01-01. History starts at that date or the first "
            "available trading date. Check coverage warnings. Set include_incomplete=false "
            "when you need completed monthly candles."
        ),
        json_response=True,
        stateless_http=True,
        streamable_http_path="/mcp",
        transport_security=transport_security(),
        # No auth provider, token verifier, API key or OAuth configuration.
    )

    @mcp.tool(
        name="get_monthly_history",
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
        ),
    )
    async def get_monthly_history(
        symbol: Annotated[str, Field(min_length=1, max_length=43, description="Indian stock symbol, e.g. TCS or RELIANCE.NS")],
        exchange: Literal["NSE", "BSE"] | None = None,
        adjusted: bool = False,
        include_incomplete: bool = True,
        start_date: date = START_DATE,
    ) -> MonthlyHistory:
        """Get monthly OHLC, volume and coverage from start_date (default 2000-01-01) through today.

        NSE is the default; .BO selects BSE. No pre-listing candles are fabricated.
        The ongoing month is included by default and marked is_complete=false.
        The server needs an Upstox token; clients do not. adjusted must remain false.
        """
        try:
            # Provider requests are synchronous; keep them off the MCP event loop.
            return await asyncio.to_thread(
                service.get_monthly_history, symbol, exchange, adjusted, include_incomplete, start_date,
            )
        except HistoryError as exc:
            raise ToolError(f"History error {exc.status_code}: {exc}") from exc

    return mcp


if __name__ == "__main__":
    create_mcp_server().run(transport="stdio")
