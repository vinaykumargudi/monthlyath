"""Protocol tests use synthetic prices; stdio discovery runs a real subprocess."""

import asyncio
from datetime import date
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pandas as pd
import pytest

from app import service
from app.history import HistoryError
from app.main import create_app

HEADERS = {"Accept": "application/json, text/event-stream"}


def request(client, method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=HEADERS)


@pytest.fixture
def mcp_client(monkeypatch):
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.setattr(service, "india_today", lambda: date(2026, 10, 11))
    monkeypatch.setattr(service, "fetch_daily_history", lambda *args: pd.DataFrame(
        {"Open": [100, 120], "High": [130, 150], "Low": [90, 110],
         "Close": [125, 140], "Volume": [1000, 2000]},
        index=pd.DatetimeIndex(["2026-09-30", "2026-10-09"], tz="Asia/Kolkata"),
    ))
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        response = request(client, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "monthlyath-test", "version": "1.0"},
        })
        assert response.status_code == 200
        assert not response.history  # exact /mcp, no redirect needed
        assert "www-authenticate" not in response.headers
        assert "mcp-session-id" not in response.headers  # stateless
        result = response.json()["result"]
        assert result["serverInfo"]["name"] == "monthlyath"
        client.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        initialized = request(client, "notifications/initialized", request_id=None)
        assert initialized.status_code == 202
        yield client


def test_discover_tool_without_auth(mcp_client):
    response = request(mcp_client, "tools/list")
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["get_monthly_history"]
    tool = tools[0]
    assert tool["inputSchema"]["required"] == ["symbol"]
    assert tool["annotations"]["readOnlyHint"] is True
    assert "data" in tool["outputSchema"]["properties"]


def test_tool_matches_rest_and_excludes_current_month(mcp_client):
    result = request(mcp_client, "tools/call", {
        "name": "get_monthly_history",
        "arguments": {"symbol": "TCS", "include_incomplete": False},
    }).json()["result"]
    assert result["isError"] is False
    data = result["structuredContent"]
    rest = mcp_client.get("/api/stocks/TCS/monthly?include_incomplete=false").json()
    assert data == rest
    assert data["count"] == 1
    assert data["data"][0]["close"] == 125
    assert data["requested_start"] == "2000-01-01"


def test_provider_error_is_mcp_tool_error(mcp_client, monkeypatch):
    def rate_limit(*args):
        raise HistoryError(503, "Upstox rate limit reached. Retry after 60 seconds.")
    monkeypatch.setattr(service, "fetch_daily_history", rate_limit)
    result = request(mcp_client, "tools/call", {
        "name": "get_monthly_history", "arguments": {"symbol": "TCS"},
    }).json()["result"]
    assert result["isError"] is True
    assert "503" in result["content"][0]["text"]
    assert "60 seconds" in result["content"][0]["text"]


def test_invalid_symbol_is_mcp_tool_error(mcp_client):
    result = request(mcp_client, "tools/call", {
        "name": "get_monthly_history", "arguments": {"symbol": "^INVALID"},
    }).json()["result"]
    assert result["isError"] is True
    assert "422" in result["content"][0]["text"]


def test_mcp_optional_start_date_matches_rest(mcp_client):
    result = request(mcp_client, "tools/call", {
        "name": "get_monthly_history",
        "arguments": {"symbol": "TCS", "start_date": "2026-10-01"},
    }).json()["result"]
    assert result["isError"] is False
    body = result["structuredContent"]
    assert body == mcp_client.get("/api/stocks/TCS/monthly?start_date=2026-10-01").json()
    assert body["requested_start"] == "2026-10-01"
    assert body["count"] == 1


def test_no_oauth_metadata_and_bad_origin_rejected(mcp_client):
    assert mcp_client.get("/.well-known/oauth-protected-resource").status_code == 404
    response = mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={**HEADERS, "Origin": "https://unrelated.example"})
    assert response.status_code == 403


def test_public_hostname_needs_no_credentials(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://monthlyath.example.com")
    with TestClient(create_app(), base_url="https://monthlyath.example.com") as client:
        response = request(client, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "public-test", "version": "1.0"},
        })
        assert response.status_code == 200
        assert "www-authenticate" not in response.headers


def test_stdio_handshake_and_error_call_from_another_directory(tmp_path):
    script = Path(__file__).resolve().parents[1] / "mcp_server.py"

    async def check():
        async with asyncio.timeout(20):
            params = StdioServerParameters(command=sys.executable, args=[str(script)], cwd=str(tmp_path))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    result = await session.initialize()
                    assert result.serverInfo.name == "monthlyath"
                    tools = await session.list_tools()
                    assert tools.tools[0].name == "get_monthly_history"
                    call = await session.call_tool("get_monthly_history", {"symbol": "^INVALID"})
                    assert call.isError
                    assert "422" in call.content[0].text

    asyncio.run(check())


def test_client_configuration_files():
    root = Path(__file__).resolve().parents[1]
    http_config = json.loads((root / "mcp.json").read_text())
    assert http_config["mcpServers"]["monthlyath"] == {"url": "http://127.0.0.1:8000/mcp"}
    stdio = json.loads((root / "mcp.stdio.example.json").read_text())["mcpServers"]["monthlyath"]
    assert set(stdio) == {"command", "args"}
