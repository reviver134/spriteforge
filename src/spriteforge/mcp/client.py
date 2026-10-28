"""Official MCP SDK 1.x transport adapter; no custom protocol implementation."""

import os
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


class MCPClient:
    @asynccontextmanager
    async def connect(self, server):
        if server.transport == "stdio":
            environment = {child: os.environ[source] for child, source in server.env.items()}
            params = StdioServerParameters(
                command=server.command, args=server.args, env=environment
            )
            # Server stderr can echo credentials; don't forward it to logs.
            with open(os.devnull, "w") as errors:  # noqa: ASYNC230 -- local null device only
                async with (
                    stdio_client(params, errlog=errors) as (read, write),
                    ClientSession(
                        read, write, read_timeout_seconds=timedelta(seconds=30)
                    ) as session,
                ):
                    await session.initialize()
                    yield session
        else:
            headers = {name: os.environ[source] for name, source in server.headers_env.items()}
            async with (
                httpx.AsyncClient(headers=headers, timeout=30) as http,
                streamable_http_client(server.url, http_client=http) as (read, write, _),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session,
            ):
                await session.initialize()
                yield session

    async def discover(self, server):
        async with self.connect(server) as session:
            tools, cursor, seen = [], None, set()
            for _ in range(100):
                result = await session.list_tools(cursor=cursor)
                tools.extend(t.model_dump(mode="json", exclude_none=True) for t in result.tools)
                cursor = result.nextCursor
                if not cursor:
                    return tools
                if cursor in seen:
                    raise ValueError("Repeated MCP tool-list cursor")
                seen.add(cursor)
            raise ValueError("MCP tool discovery page limit exceeded")

    async def call(self, server, name, arguments):
        async with self.connect(server) as session:
            return (await session.call_tool(name, arguments)).model_dump(mode="json")
