"""Minimal read-only MCP stdio client for configured research tools."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "mcp_servers.json"


class MCPError(RuntimeError):
    """Raised when an MCP server cannot complete a request."""


class MCPStdioClient:
    """Small JSON-RPC client using MCP's Content-Length stdio framing."""

    def __init__(self, command: str, args: list[str] | None = None) -> None:
        self.command = command
        self.args = args or []
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0

    def __enter__(self) -> "MCPStdioClient":
        command = [self.command, *self.args]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=os.environ.copy(),
        )
        self._request_id = 0
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "my-fund-ai-assistant", "version": "1.0"},
            },
        )
        self._notify("notifications/initialized", {})
        return self

    def __exit__(self, *_: object) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=5)
        self._process = None

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        if result.get("isError"):
            raise MCPError(self._text_from_result(result))
        structured = result.get("structuredContent")
        return structured if structured is not None else self._text_from_result(result)

    def list_tools(self) -> Any:
        return self._request("tools/list", {})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        while True:
            message = self._read()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise MCPError(str(error.get("message", error)))
            return message.get("result", {})

    def _write(self, message: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise MCPError("MCP server is not running")
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        self._process.stdin.write(
            f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        )
        self._process.stdin.flush()

    def _read(self) -> dict[str, Any]:
        if not self._process or not self._process.stdout:
            raise MCPError("MCP server is not running")
        headers: dict[str, str] = {}
        while True:
            line = self._process.stdout.readline()
            if not line:
                raise MCPError("MCP server closed the stdio connection")
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("ascii", errors="replace").partition(":")
            headers[key.lower().strip()] = value.strip()
        try:
            length = int(headers["content-length"])
            return json.loads(self._process.stdout.read(length).decode("utf-8"))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise MCPError("Invalid JSON-RPC response from MCP server") from exc

    @staticmethod
    def _text_from_result(result: dict[str, Any]) -> Any:
        texts = [
            item.get("text", "")
            for item in result.get("content", [])
            if item.get("type") == "text"
        ]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except (TypeError, json.JSONDecodeError):
                return texts[0]
        return "\n".join(texts)


def load_mcp_server(name: str = "vibe-trading") -> MCPStdioClient:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    server = config.get("servers", {}).get(name)
    if not server or not server.get("enabled", True):
        raise MCPError(f"MCP server is not enabled: {name}")
    return MCPStdioClient(server["command"], server.get("args", []))
