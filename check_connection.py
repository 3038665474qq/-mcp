"""MCP protocol checks. Browser/page or notebook access is explicitly opt-in."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from configure import server_configs


async def check(name: str, config: dict, live: bool) -> dict:
    params = StdioServerParameters(command=config["command"], args=config["args"],
                                   cwd=config["cwd"], env={**os.environ, **config.get("env", {})})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=75)) as session:
            info = await session.initialize()
            response = await session.list_tools()
            result = {"name": name, "server": info.serverInfo.model_dump(),
                      "tools": [t.name for t in response.tools], "handshake": "ok"}
            if live:
                tool = "list_notebooks" if name == "yinxiang_local" else "list_pages"
                called = await session.call_tool(tool, {})
                result["live_tool"] = tool
                result["live_ok"] = not called.isError
                # Avoid recording private notebook names or Chrome page URLs.
                result["response_blocks"] = len(called.content)
                if called.isError:
                    result["error"] = " ".join(getattr(c, "text", "") for c in called.content)[:1200]
            return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", choices=["yinxiang_local", "chrome_current"])
    parser.add_argument("--server", choices=["yinxiang_local", "chrome_current"])
    args = parser.parse_args()
    results = []
    for name, config in server_configs().items():
        if args.server and name != args.server:
            continue
        try:
            result = await check(name, config, args.live == name)
        except Exception as exc:
            result = {"name": name, "handshake": "error", "error": str(exc)}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(r.get("handshake") != "ok" or r.get("live_ok") is False for r in results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
