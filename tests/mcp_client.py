"""Talk to the MCP server the way an external client would: spawn it over stdio, list and call tools.

    .venv/bin/python tests/mcp_client.py
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
SERVER = StdioServerParameters(command=sys.executable, args=["-m", "insidegame.mcp_server"], cwd=str(ROOT))

CALLS = [
    ("list_matches", {}),
    ("get_match_state", {"match_id": "remix-42", "minute": 7}),
    ("get_key_moments", {"match_id": "remix-42", "kind": "goal"}),
    ("get_player_stats", {"match_id": "remix-42", "sort_by": "xg", "limit": 2}),
    ("get_commentary", {"match_id": "remix-42", "profile": "analyst", "lang": "zh"}),
    ("get_match_state", {"match_id": "no-such-match", "minute": 1}),
]


def text_of(result):
    return "".join(getattr(c, "text", "") for c in result.content)


async def run():
    async with Client(SERVER) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))
        results = {}
        for name, args in CALLS:
            result = await client.call_tool(name, args)
            body = text_of(result)
            results[(name, json.dumps(args, sort_keys=True))] = (result.is_error, body)
            print(f"\n{name}({args}) -> {'ERROR ' if result.is_error else ''}{body[:300]}")
        return [t.name for t in tools.tools], results


if __name__ == "__main__":
    asyncio.run(run())
