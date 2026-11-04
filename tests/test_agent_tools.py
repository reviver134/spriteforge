"""Agents of a game build call their granted tools (built-in MCP servers, skills, the user's
MCP tools that need no confirmation) for a few rounds before they answer."""

import json

from pydantic import BaseModel
from test_game_stage import to_design_gate

from spriteforge.app.agents_view import thread
from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.agent_tools import Toolbox
from spriteforge.game.pipeline import Models
from spriteforge.mcp.registry import Registry, Server, Servers
from spriteforge.providers.base import ChatResult
from spriteforge.providers.mock import MockLLMProvider
from spriteforge.stages.game import GameContext


class Answer(BaseModel):
    pick: str


def call(name: str, arguments: dict, ident: str = "c1") -> ChatResult:
    """A tool call the way Spark returns it (OpenAI-style, arguments as a JSON string)."""
    return ChatResult(tool_calls=[{"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments)}}])  # fmt: skip


class Seen(MockLLMProvider):
    def __init__(self, budget, responses):
        super().__init__(budget, responses)
        self.requests = []

    def chat(self, request):
        self.requests.append(request.model_copy(deep=True))
        return super().chat(request)


def setup(tmp_path):
    ws = init_workspace(tmp_path / "ws")
    store = Store(ws / "state.sqlite3")
    run = store.create_run({"workflow": "test", "budget": BudgetConfig().model_dump()})
    budget = Budget(store, run.id, BudgetConfig())
    return ws, store, run.id, budget


def test_an_agent_looks_things_up_then_answers(tmp_path):
    ws, store, run_id, budget = setup(tmp_path)
    provider = Seen(budget, [call("library_search", {"query": "slime"}),
                             call("load_skill", {"name": "pixel-art-prompting"}, "c2"),
                             {"pick": "slime"}])  # fmt: skip
    models = Models(load_config(), budget, mock=True, provider=provider)
    models.toolbox = Toolbox(store, run_id, ws)
    answer = models.ask("Pixel Artist", "Pick a sprite", Answer, Answer(pick="x"))
    assert answer.pick == "slime"
    first, _, last = provider.requests
    assert {t["function"]["name"] for t in first.tools} == {"library_search", "load_skill"}
    assert "library.library_search" in first.messages[0].content  # the system prompt names them
    tool_messages = [m for m in last.messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["c1", "c2"]
    assert "pixel" in tool_messages[1].content.lower()  # the skill's body came back
    events = [e for e in store.events(run_id) if e.type == "agent.tool"]
    assert [(e.agent, e.payload["tool"]) for e in events] == [
        ("Pixel Artist", "library.library_search"), ("Pixel Artist", "skill")]
    lines = [i["text"] for i in thread(store, run_id, "Pixel Artist") if i["kind"] == "progress"]
    assert lines[0] == "Looked up with library.library_search (query: slime)"


def test_rounds_are_capped_and_a_bad_tool_is_an_answer_not_a_crash(tmp_path):
    ws, store, run_id, budget = setup(tmp_path)
    provider = Seen(budget, [call("no_such_tool", {}), call("library_search", {"query": 1}, "c2"),
                             call("library_search", {}, "c3"), {"pick": "done"}])  # fmt: skip
    models = Models(load_config(), budget, mock=True, provider=provider)
    models.toolbox, models.tool_rounds = Toolbox(store, run_id, ws), 3
    assert models.ask("Art Director", "Pick", Answer, Answer(pick="x")).pick == "done"
    assert len(provider.requests) == 4 and provider.requests[-1].tools == []  # must answer
    assert "no tool named no_such_tool" in provider.requests[1].messages[-1].content


def test_without_a_toolbox_or_offline_there_is_one_plain_call(tmp_path):
    ws, store, run_id, budget = setup(tmp_path)
    provider = Seen(budget, [{"pick": "a"}])
    models = Models(load_config(), budget, mock=True, provider=provider)
    assert models.ask("Art Director", "Pick", Answer, Answer(pick="x")).pick == "a"
    assert provider.requests[0].tools == []
    offline = Models(load_config(), budget, mock=True)  # stand-in model: no tool calls
    offline.toolbox = Toolbox(store, run_id, ws)
    assert offline.ask("Art Director", "Pick", Answer, Answer(pick="fallback")).pick == "fallback"


def test_user_mcp_tools_need_a_grant_and_no_confirmation(tmp_path):
    ws, store, run_id, _ = setup(tmp_path)
    server = Server(command="my-server", tools=[{"name": "lookup", "description": "Look up",
                                                  "inputSchema": {"type": "object"}}],
                    enabled_roles={"Art Director": ["lookup"]})  # fmt: skip
    server.discovered_fingerprint = server.fingerprint()
    Registry(ws).write(Servers(mcpServers={"mine": server}))
    box = Toolbox(store, run_id, ws)
    assert "mine__lookup" not in box.for_role("Art Director")  # confirmation is on by default
    Registry(ws).policy("mine", "lookup", False)
    assert box.for_role("Art Director")["mine__lookup"].label == "mine.lookup"
    assert "mine__lookup" not in box.for_role("Programmer")  # not granted to it
    assert "Your MCP tool mine.lookup" in box.describe("Art Director")


def test_game_record_tools_once_the_record_exists_and_plans_list_the_tools(tmp_path):
    store, ident = to_design_gate(tmp_path)  # design v1 is written into the game record
    ctx = GameContext(store, ident)
    assert ctx.models.toolbox is ctx.toolbox
    tools = ctx.toolbox.for_role("Menu Designer")
    assert {"game_index", "game_show", "game_context"} <= set(tools)
    assert "design" in ctx.toolbox.call("Menu Designer", tools["game_index"], {})
    plan = next(i["report"] for i in thread(store, ident, "Art Director") if i["kind"] == "report")
    used = next(s for s in plan["sections"] if s["title"] == "Tools and skills I can use")
    assert "MCP tool library.library_search" in used["points"]


def test_the_switch_turns_the_tools_off(tmp_path):
    store, ident = to_design_gate(tmp_path)
    run = store.get_run(ident)
    run.config["settings"]["game"]["agent_tools"] = False
    with store.connect() as db:
        db.execute("UPDATE runs SET config=? WHERE id=?", (json.dumps(run.config), ident))
    assert GameContext(store, ident).models.toolbox is None
