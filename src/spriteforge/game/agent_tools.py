"""The tools an agent of a game build may call before it answers.

Each role's `allowed_tools` (agents/roles/*.md) names the built-in MCP tools it may use as
`server.tool`: `library.*` (the local art library) and `game_db.*` (the game's record, once it
exists). They are the same FastMCP servers `spriteforge library mcp` and `spriteforge game db
mcp` serve, called in-process. A role with skills (its own and the user's grants) gets
`load_skill`. The user's MCP servers granted to the role (mcp.json) are offered only for tools
the human set to run without confirmation: a build cannot stop half way to ask.

Every tool here only reads. Each call is logged as an `agent.tool` event (the studio shows it
in the agent's chat); results are cut to `MAX_RESULT` characters.
"""

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from spriteforge.agents.roles import Role, load_roles
from spriteforge.core.security import redact
from spriteforge.mcp.registry import Registry
from spriteforge.skills.catalog import SkillCatalog

MAX_RESULT = 6000
MAX_SKILL = 12000  # a skill is read whole (the vendored ones are about 9 KB)
SERVERS = ("library", "game_db")


def run_async(coro):
    """Run a coroutine from sync code, also when this thread already runs an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    out: dict = {}

    def work():
        try:
            out["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 -- re-raised in the caller's thread
            out["error"] = exc

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    if "error" in out:
        raise out["error"]
    return out["value"]


def text_of(result) -> str:
    """A FastMCP `call_tool` result (content blocks, structured) or an MCP client result."""
    if isinstance(result, tuple):
        blocks, structured = result
        text = "\n".join(getattr(b, "text", "") for b in blocks if getattr(b, "text", ""))
        return text or json.dumps(structured)
    if isinstance(result, dict):  # MCPClient.call: CallToolResult as JSON
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("text"))
        return ("Error: " if result.get("isError") else "") + (text or json.dumps(result))
    return str(result)


def clip(text: str, limit: int = MAX_RESULT) -> str:
    return text if len(text) <= limit else text[:limit] + "\n… (cut)"


@dataclass
class AgentTool:
    name: str  # what the model calls (letters, digits, _ and - only)
    label: str  # how people read it: library.library_search, skill, my-server.tool
    description: str
    schema: dict
    run: Callable[[dict], str]
    limit: int = MAX_RESULT  # longest answer, in characters

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.schema}}  # fmt: skip


class Toolbox:
    """The tools of one run's agents. `game_db` opens the run's game record (None until the
    record is written: then the `game_db.*` tools are not offered)."""

    def __init__(self, store, run_id: str, workspace: Path, game_db: Callable | None = None,
                 roles: dict[str, Role] | None = None):  # fmt: skip
        self.store, self.run_id, self.workspace = store, run_id, workspace
        self.game_db = game_db
        self.roles = roles or load_roles()
        self.skills = SkillCatalog(workspace)

    def servers(self) -> dict:
        from spriteforge.mcp.library import create_server as library_server

        out = {"library": library_server()}
        db = self.game_db() if self.game_db else None
        if db is not None:
            from spriteforge.mcp.gamedb import create_server as record_server

            out["game_db"] = record_server(Path(db.root), db)
        return out

    def for_role(self, role: str) -> dict[str, AgentTool]:
        r = self.roles.get(role)
        if r is None:
            return {}
        tools: dict[str, AgentTool] = {}
        wanted = [t.split(".", 1) for t in r.allowed_tools if t.split(".", 1)[0] in SERVERS]
        if wanted:
            servers = self.servers()
            for server_name, tool_name in wanted:
                server = servers.get(server_name)
                if server is None:
                    continue
                listed = {t.name: t for t in run_async(server.list_tools())}
                if tool_name not in listed:
                    continue
                tool = listed[tool_name]
                tools[tool_name] = AgentTool(
                    tool_name, f"{server_name}.{tool_name}", tool.description or "",
                    tool.inputSchema,
                    lambda args, s=server, n=tool_name: text_of(run_async(s.call_tool(n, args))),
                )  # fmt: skip
        if skills := self.skills.headers(r):
            listing = "; ".join(f"{n}: {h.description}" for n, (h, _) in skills.items())
            tools["load_skill"] = AgentTool(
                "load_skill", "skill", "Read one of your skills (how-to notes) in full. "
                f"Your skills: {listing}",
                {"type": "object", "properties": {"name": {"type": "string",
                                                           "enum": sorted(skills)}},
                 "required": ["name"], "additionalProperties": False},
                lambda args, role=r: self.skills.load(role, args["name"]), MAX_SKILL,
            )  # fmt: skip
        tools.update(self.user_tools(r))
        return tools

    def user_tools(self, role: Role) -> dict[str, AgentTool]:
        """The user's MCP tools granted to this role that run without asking the human."""
        from spriteforge.mcp.service import MCPService

        out = {}
        for qualified, (server, definition) in Registry(self.workspace).allowed(role).items():
            if server.confirmation.get(definition["name"], True):
                continue
            name = qualified.replace(".", "__")
            out[name] = AgentTool(
                name, qualified, definition.get("description", ""),
                definition.get("inputSchema", {"type": "object"}),
                lambda args, q=qualified: text_of(run_async(MCPService(self.store).call(
                    self.run_id, role, q, args, uuid4().hex))),
            )  # fmt: skip
        return out

    def call(self, role: str, tool: AgentTool, arguments: dict) -> str:
        """Run one call; a failure is the tool's answer (the agent reads it and goes on)."""
        try:
            text, ok = clip(redact(tool.run(arguments)), tool.limit), True
        except Exception as exc:  # noqa: BLE001 -- the model sees the error, the build goes on
            text, ok = f"Error: {type(exc).__name__}: {exc}"[:500], False
        self.store.append_event(self.run_id, role, "agent.tool",
                                {"tool": tool.label, "arguments": redact(arguments), "ok": ok,
                                 "chars": len(text)})  # fmt: skip
        return text

    def describe(self, role: str) -> list[str]:
        """What an agent can use, for its plan (no tool is listed, called or opened here
        beyond the role's grants)."""
        r = self.roles.get(role)
        if r is None:
            return []
        out = [f"MCP tool {t}" for t in r.allowed_tools if t.split(".", 1)[0] in SERVERS]
        out += [f"Skill {n}" for n in self.skills.headers(r)]
        out += [f"Your MCP tool {q}" for q, (s, d) in Registry(self.workspace).allowed(r).items()
                if not s.confirmation.get(d["name"], True)]  # fmt: skip
        return out
