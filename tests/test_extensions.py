import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

from spriteforge.agents.models import RoleOutput
from spriteforge.agents.roles import load_roles
from spriteforge.agents.runtime import AgentRuntime, AgentTask
from spriteforge.app.server import create_app
from spriteforge.config import BudgetConfig
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.mcp.client import MCPClient
from spriteforge.mcp.registry import Server
from spriteforge.mcp.service import ConfirmationRequired, MCPService
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.providers.base import ChatResult
from spriteforge.providers.mock import MockLLMProvider
from spriteforge.skills.catalog import SkillCatalog


class FakeClient:
    def __init__(self):
        self.calls = []

    async def discover(self, server):
        return [
            {
                "name": "write",
                "description": "Write an asset",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            }
        ]

    async def call(self, server, name, arguments):
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "saved"}], "isError": False}


def context(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "extension-test"})
    client = FakeClient()
    service = MCPService(store, client)
    service.registry.add("art", Server(command="unused", enabled_roles={"Programmer": ["write"]}))
    asyncio.run(service.discover("art"))
    service.registry.enable("art", "Pixel Artist")
    return store, run.id, service, client


def test_skill_install_scope_progressive_load_and_remove(tmp_path):
    source = tmp_path / "sample"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: sample-art\ndescription: Improve silhouettes\n---\nBODY_ONLY_AFTER_LOAD"
    )
    workspace = tmp_path / "workspace"
    catalog = SkillCatalog(workspace)
    catalog.add(str(source))
    roles = load_roles()
    assert "sample-art" in catalog.available(roles["Pixel Artist"])
    assert "sample-art" not in catalog.available(roles["Programmer"])
    with pytest.raises(PermissionError):
        catalog.load(roles["Programmer"], "sample-art")
    with pytest.raises(ValueError):
        catalog.load(roles["Pixel Artist"], "sample-art", "../../secret.txt")
    store = Store(workspace / "state.sqlite3")
    run = store.create_run({})
    requests = []

    class Provider:
        def chat(self, request):
            requests.append(request.model_copy(deep=True))
            if len(requests) == 1:
                return ChatResult(
                    tool_calls=[
                        {"id": "load", "name": "load_skill", "arguments": {"name": "sample-art"}}
                    ]
                )
            return ChatResult(content='{"summary":"done"}')

    runtime = AgentRuntime(
        store, run.id, KnowledgeStore(workspace / "memory", store, run.id), Provider()
    )
    asyncio.run(runtime.run(roles["Pixel Artist"], AgentTask(goal="art"), RoleOutput))
    assert "BODY_ONLY_AFTER_LOAD" not in requests[0].model_dump_json()
    assert "BODY_ONLY_AFTER_LOAD" in requests[1].model_dump_json()
    catalog.remove("sample-art")
    assert "sample-art" not in catalog.all()
    (source / "SKILL.md").write_text("---\nname: ../escape\ndescription: invalid\n---\nbody")
    with pytest.raises(ValueError):
        catalog.add(str(source))


def test_mcp_confirmation_permissions_replay_and_redaction(tmp_path, monkeypatch):
    store, ident, service, client = context(tmp_path)
    roles = load_roles()
    assert "Programmer" not in service.registry.read().mcpServers["art"].enabled_roles
    monkeypatch.setenv("META_API_KEY", "test-key-never-log")
    with pytest.raises(PermissionError):
        asyncio.run(
            service.call(ident, roles["Programmer"], "art.write", {"path": "test-key-never-log"})
        )
    with pytest.raises(ConfirmationRequired) as pending:
        asyncio.run(
            service.call(ident, roles["Pixel Artist"], "art.write", {"path": "hero.png"}, "one")
        )
    assert pending.value.ident == "one" and not client.calls
    assert store.get_run(ident).status == "paused"
    # Reconstruct service and store as after a process restart.
    service = MCPService(Store(store.path), client)
    with TestClient(create_app(store, mcp_service=service)) as http:
        assert (
            http.post(
                "/api/mcp/art/enable",
                json={"role": "Programmer", "enabled": True},
                headers={"Origin": "https://unrelated.example"},
            ).status_code
            == 403
        )
        assert http.get(f"/api/runs/{ident}/mcp/confirmations").json()[0]["tool"] == "art.write"
        response = http.post(f"/api/runs/{ident}/mcp/confirmations/one", json={"approve": True})
        assert response.status_code == 200
        assert (
            http.post(
                f"/api/runs/{ident}/mcp/confirmations/one", json={"approve": True}
            ).status_code
            == 409
        )
    assert len(client.calls) == 1
    asyncio.run(
        service.call(ident, roles["Pixel Artist"], "art.write", {"path": "hero.png"}, "one")
    )
    assert len(client.calls) == 1
    assert "test-key-never-log" not in json.dumps([e.model_dump() for e in store.events(ident)])
    with pytest.raises(ConfirmationRequired):
        asyncio.run(
            service.call(
                ident, roles["Pixel Artist"], "art.write", {"path": "denied.png"}, "denied"
            )
        )
    asyncio.run(service.respond(ident, "denied", False, roles["Pixel Artist"]))
    assert asyncio.run(
        service.call(ident, roles["Pixel Artist"], "art.write", {"path": "denied.png"}, "denied")
    )["isError"]
    assert len(client.calls) == 1
    with pytest.raises(ConfirmationRequired):
        asyncio.run(
            service.call(ident, roles["Pixel Artist"], "art.write", {"path": "second.png"}, "two")
        )
    service.registry.disable("art")
    with pytest.raises(PermissionError):
        asyncio.run(service.respond(ident, "two", True, roles["Pixel Artist"]))
    assert len(client.calls) == 1


def test_runtime_resumes_confirmed_call_without_regeneration(tmp_path):
    store, ident, service, client = context(tmp_path)
    provider = MockLLMProvider(
        Budget(store, ident, BudgetConfig()),
        [
            ChatResult(
                tool_calls=[
                    {"id": "write1", "name": "art.write", "arguments": {"path": "hero.png"}}
                ]
            ),
            {"summary": "saved"},
        ],
    )
    role = load_roles()["Pixel Artist"]
    runtime = AgentRuntime(
        store, ident, KnowledgeStore(tmp_path / "memory", store, ident), provider, mcp=service
    )
    task = AgentTask(id="art-task", goal="save hero")
    with pytest.raises(ConfirmationRequired):
        asyncio.run(runtime.run(role, task, RoleOutput))
    asyncio.run(service.respond(ident, "art-task:0:write1", True, role))
    artifact = asyncio.run(runtime.run(role, task, RoleOutput))
    assert RoleOutput.model_validate_json(artifact.data).summary == "saved"
    assert len(client.calls) == 1


def test_sdk_stdio_read_server_and_escape(tmp_path):
    (tmp_path / "art.txt").write_text("pixel-art")
    server = Server(
        command=sys.executable, args=["-m", "spriteforge.mcp.filesystem_read", str(tmp_path)]
    )

    async def exercise():
        client = MCPClient()
        assert (await client.discover(server))[0]["name"] == "read_text"
        result = await client.call(server, "read_text", {"path": "art.txt"})
        assert "pixel-art" in json.dumps(result) and not result["isError"]
        result = await client.call(server, "read_text", {"path": "../outside.txt"})
        assert result["isError"]

    asyncio.run(exercise())


def test_policy_disable_confirmation_and_definition_change_revokes(tmp_path):
    _store, ident, service, client = context(tmp_path)
    role = load_roles()["Pixel Artist"]
    service.registry.policy("art", "write", False)
    asyncio.run(service.call(ident, role, "art.write", {"path": "hero.png"}))
    assert len(client.calls) == 1 and not service.pending(ident)
    server = service.registry.read().mcpServers["art"]
    service.registry.discovered(
        "art", server.fingerprint(), [{**server.tools[0], "description": "Changed"}]
    )
    assert not service.registry.allowed(role)
