"""Safe, read-only query helpers backed by the configured MCP server."""

from __future__ import annotations

from datetime import date
from typing import Any

try:
    from .mcp_client import MCPError, load_mcp_server
except ImportError:
    from mcp_client import MCPError, load_mcp_server


def query_market_data(
    codes: list[str],
    start_date: str,
    end_date: str | None = None,
    source: str = "auto",
) -> Any:
    """Query OHLCV data through Vibe-Trading MCP."""
    with load_mcp_server() as client:
        return client.call(
            "get_market_data",
            {
                "codes": codes,
                "start_date": start_date,
                "end_date": end_date or date.today().isoformat(),
                "source": source,
                "interval": "1D",
                "max_rows": 250,
            },
        )


def query_positions() -> Any:
    """Read broker positions when a read-only connector is configured."""
    with load_mcp_server() as client:
        return client.call("trading_positions", {})


def query_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call only tools explicitly allowed by config/mcp_servers.json."""
    import json
    try:
        from .mcp_client import CONFIG_PATH
    except ImportError:
        from mcp_client import CONFIG_PATH

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    allowed = config["servers"]["vibe-trading"].get("read_only_tools", [])
    if tool_name not in allowed:
        raise MCPError(f"Tool is not in the read-only allowlist: {tool_name}")
    with load_mcp_server() as client:
        return client.call(tool_name, arguments or {})
