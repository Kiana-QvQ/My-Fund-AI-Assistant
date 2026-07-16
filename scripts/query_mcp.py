"""Query a read-only Vibe-Trading MCP tool from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from mcp_client import MCPError  # noqa: E402
from query_tools import query_tool  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Query a read-only MCP tool")
    parser.add_argument("tool", help="Tool name, for example get_market_data")
    parser.add_argument(
        "--arguments",
        default="{}",
        help='JSON arguments, for example \'{"codes":["AAPL.US"]}\'',
    )
    args = parser.parse_args()
    try:
        arguments = json.loads(args.arguments)
        result = query_tool(args.tool, arguments)
    except (json.JSONDecodeError, MCPError, OSError) as exc:
        raise SystemExit(f"MCP query failed: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
