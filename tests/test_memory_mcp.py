import json
import sys
from datetime import timedelta

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_sdk_stdio_session_and_private_scope_gate(tmp_path):
    async def exercise():
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "spriteforge", "memory", "mcp", "--workspace", str(tmp_path)],
        )
        async with (
            stdio_client(server) as (read, write),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "memory_search",
                "memory_index",
                "memory_show",
            }
            result = await session.call_tool(
                "memory_search", {"query": "metroidvania map", "token_budget": 512}
            )
            output = json.dumps(result.model_dump(mode="json"))
            assert "genres/metroidvania" in output and "human-reviewed" in output
            assert not result.isError
            index = await session.call_tool("memory_index", {"directory": "genres"})
            assert "metroidvania.md" in json.dumps(index.model_dump(mode="json"))
            full = await session.call_tool("memory_show", {"id": "genres/metroidvania"})
            assert "Adaptive questions" in json.dumps(full.model_dump(mode="json"))
            denied = await session.call_tool(
                "memory_search", {"query": "preferences", "scopes": ["private"]}
            )
            assert denied.isError

    anyio.run(exercise)
