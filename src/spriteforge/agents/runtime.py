"""Bounded provider/tool loop; every cross-agent result is a persisted typed artifact."""

import asyncio
import inspect
import json
import threading
from collections.abc import Callable
from contextvars import copy_context
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spriteforge.config import RouterConfig, load_config
from spriteforge.core.security import redact
from spriteforge.core.state import Artifact, Store
from spriteforge.mcp.service import ConfirmationRequired, MCPService
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.providers.base import ChatRequest, LLMProvider, Message
from spriteforge.skills.catalog import SkillCatalog

from .cancellation import cancelled, check_active
from .roles import Role
from .router import build_catalog, token_units
from .routing_state import RoutingStore


class AgentTask(BaseModel):
    id: str | None = None
    goal: str
    inputs: list[str] = Field(default_factory=list)
    memory_budget: int = Field(default=768, ge=0)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    arguments: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    arguments: type[BaseModel]
    result: type[BaseModel]
    handler: Callable
    description: str = ""

    def spec(self, name: str) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": self.description,
                "parameters": self.arguments.model_json_schema(),
            },
        }


async def invoke(function, *args):
    check_active()
    if inspect.iscoroutinefunction(function):
        return await function(*args)
    # Daemon threads do not make asyncio.run wait for a timed-out synchronous call.
    # Adapters must check_active() before publishing side effects after a provider call.
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    context = copy_context()

    def finish(value, error):
        if not future.done():
            if error is None:
                future.set_result(value)
            else:
                future.set_exception(error)

    def work():
        try:
            value, error = context.run(function, *args), None
        except Exception as exc:  # noqa: BLE001 -- forward tool/provider errors to the caller
            value, error = None, exc
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(finish, value, error)
            except RuntimeError:
                pass  # The caller already timed out and closed its event loop.

    threading.Thread(target=work, daemon=True).start()
    result = await future
    check_active()
    return await result if inspect.isawaitable(result) else result


class AgentRuntime:
    def __init__(
        self,
        store: Store,
        run_id: str,
        memory: KnowledgeStore,
        provider: LLMProvider | dict[str, LLMProvider],
        tools: dict[str, Tool] | None = None,
        skills: dict[str, str] | None = None,
        mcp: MCPService | None = None,
        router_config: RouterConfig | None = None,
        router_provider: LLMProvider | None = None,
    ):
        self.store, self.run_id, self.memory, self.provider = store, run_id, memory, provider
        self.tools, self.skills = tools or {}, skills or {}
        self.catalog = SkillCatalog(store.path.parent)
        self.mcp = mcp or MCPService(store)
        settings = store.get_run(run_id).config
        self.router_config = router_config or RouterConfig.model_validate(
            settings.get("settings", settings).get("router", load_config().router.model_dump())
        )
        self.router_provider = router_provider
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS agent_checkpoints (run_id TEXT, task_id TEXT, body TEXT, PRIMARY KEY(run_id,task_id))"
            )

    async def run(
        self, role: Role, task: AgentTask, schema: type[BaseModel], *, timeout: float = 60
    ) -> Artifact:
        signal = threading.Event()
        task = task.model_copy(update={"id": task.id or uuid4().hex})
        token = cancelled.set(signal)
        try:
            return await asyncio.wait_for(self._loop(role, task, schema), timeout)
        except ConfirmationRequired:
            self.store.append_event(self.run_id, role.name, "agent.paused", {"task_id": task.id})
            raise
        except Exception as exc:
            RoutingStore(self.store, self.run_id, task.id).outcome("failed")
            self.store.append_event(
                self.run_id, role.name, "agent.failed", {"error": type(exc).__name__}
            )
            raise
        finally:
            signal.set()
            cancelled.reset(token)

    async def _loop(self, role, task, schema):
        if task.id:
            prior = next(
                (a for a in self.store.artifacts(self.run_id) if a.name == f"agent/{task.id}.json"),
                None,
            )
            if prior:
                schema.model_validate_json(prior.data)
                RoutingStore(self.store, self.run_id, task.id).outcome("completed")
                return prior
        # Redact before retrieval or model dispatch, including Librarian inputs.
        snippets = await invoke(
            self.memory.retrieve, redact(task.goal), role.memory_scopes, task.memory_budget
        )
        self.store.append_event(
            self.run_id,
            role.name,
            "agent.memory",
            {"ids": [s.id for s in snippets], "scopes": role.memory_scopes},
        )
        artifacts = {a.id: a for a in self.store.artifacts(self.run_id)}
        inputs = []
        for ident in task.inputs:
            artifact = artifacts[ident]  # Only explicit artifacts from this run may be passed.
            inputs.append(
                {
                    "id": ident,
                    "name": artifact.name,
                    "mime": artifact.mime,
                    "content": artifact.data.decode("utf-8")
                    if artifact.mime == "application/json"
                    else None,
                }
            )
        # Revalidate mutable Role objects before using their permissions.
        role = Role.model_validate(role.model_dump())
        tools = {name: self.tools[name] for name in role.allowed_tools if name in self.tools}
        mcp_tools = self.mcp.registry.allowed(role)
        provider = self.provider[role.model] if isinstance(self.provider, dict) else self.provider
        catalog = build_catalog(role, self.catalog, self.skills, tools, mcp_tools)
        routing = RoutingStore(self.store, self.run_id, task.id)
        routed = await routing.prepare(
            role,
            task.goal,
            catalog,
            self.router_config,
            self.router_provider or provider,
            invoke,
            self.catalog,
            self.skills,
        )
        selected = routed.decision
        tools = {n: t for n, t in tools.items() if n in selected.selected_tools}
        mcp_tools = {n: t for n, t in mcp_tools.items() if n in selected.selected_tools}
        summaries = [c.model_dump() for c in catalog.skills if c.name in selected.selected_skills]
        skill_spec = {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "Load an allowed skill body or a selected reference; scripts are read, never executed.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "resource": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        }
        request = ChatRequest(
            messages=[
                Message(role="system", content=role.prompt + "\nGoal: " + role.goal),
                Message(
                    role="user",
                    content=json.dumps(
                        redact(
                            {
                                "task": task.model_dump(),
                                "inputs": inputs,
                                "memory": [s.model_dump() for s in snippets],
                                "skills": summaries,
                                "required_skill_bodies": {
                                    n: routed.bodies[n] for n in role.required_skills
                                },
                            }
                        )
                    ),
                ),
            ],
            tools=[tool.spec(name) for name, tool in tools.items()]
            + ([skill_spec] if summaries else [])
            + [
                {
                    "type": "function",
                    "function": {
                        "name": n,
                        "description": next(c.description for c in catalog.tools if c.name == n),
                        "parameters": t.get("inputSchema", {}),
                    },
                }
                for n, (_, t) in mcp_tools.items()
            ],
            json_schema=schema.model_json_schema(),
        )
        retried = False
        with self.store.connect() as db:
            row = db.execute(
                "SELECT body FROM agent_checkpoints WHERE run_id=? AND task_id=?",
                (self.run_id, task.id),
            ).fetchone()
        checkpoint = json.loads(row[0]) if row else None
        if checkpoint and checkpoint["role"] != role.model_dump():
            raise PermissionError("Role changed while agent was paused")
        if checkpoint:
            request = ChatRequest.model_validate(checkpoint["request"])
        start_step = checkpoint["step"] + (not checkpoint["calls"]) if checkpoint else 0
        if checkpoint and not checkpoint["calls"]:
            checkpoint = None

        def save_checkpoint(step, calls):
            body = {
                "role": role.model_dump(),
                "request": request.model_dump(mode="json"),
                "step": step,
                "calls": calls,
            }
            with self.store.connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO agent_checkpoints VALUES(?,?,?)",
                    (self.run_id, task.id, json.dumps(redact(body))),
                )

        for step in range(start_step, role.max_steps):
            from spriteforge.providers.base import ChatResult

            replay = checkpoint is not None
            result = (
                ChatResult(tool_calls=checkpoint["calls"])
                if replay
                else await invoke(provider.chat, request)
            )
            checkpoint = None
            self.store.append_event(
                self.run_id, role.name, "agent.step", {"step": step + 1, "model": role.model}
            )
            if result.tool_calls:
                if not replay:
                    request.messages.append(Message(role="assistant", tool_calls=result.tool_calls))
                for index, raw in enumerate(result.tool_calls):
                    save_checkpoint(step, result.tool_calls[index:])
                    # Accept the provider-neutral and OpenAI-compatible tool-call shapes.
                    if "function" in raw:
                        raw = {
                            "id": raw["id"],
                            "name": raw["function"]["name"],
                            "arguments": json.loads(raw["function"]["arguments"]),
                        }
                    call = ToolCall.model_validate(raw)
                    if call.name == "load_skill":
                        name = call.arguments["name"]
                        if name not in selected.selected_skills:
                            raise PermissionError(f"Skill not selected: {name}")
                        if name not in role.allowed_skills and name not in self.catalog.headers(
                            role
                        ):
                            raise PermissionError(f"Skill permission revoked: {name}")
                        resource = call.arguments.get("resource", "SKILL.md")
                        if resource == "SKILL.md":
                            body = routed.bodies[name]
                        else:
                            # References cannot evade the shared context budget.
                            body = self.catalog.load(role, name, resource)
                        # Required bodies and prior loads are accounted separately below;
                        # never append a duplicate body or an over-budget reference.
                        loaded = sum(
                            token_units(m.content or "")
                            for m in request.messages
                            if m.role == "tool" and m.content and '"body"' in m.content
                        )
                        pinned = sum(
                            token_units(json.dumps({n: routed.bodies[n]}))
                            for n in role.required_skills
                        )
                        payload = {"name": name, "body": body}
                        already_loaded = name in role.required_skills or any(
                            m.role == "tool"
                            and m.content
                            and json.loads(m.content).get("name") == name
                            and "body" in json.loads(m.content)
                            for m in request.messages
                        )
                        if already_loaded and resource == "SKILL.md":
                            payload = {"name": name, "already_loaded": True}
                        elif (
                            pinned + loaded + token_units(json.dumps(payload))
                            > self.router_config.token_budget
                        ):
                            payload = {
                                "name": name,
                                "error": "Skill context token budget exhausted",
                            }
                        else:
                            routing.used("skills", name)
                    elif "." in call.name and call.name not in tools:
                        if call.name not in mcp_tools:
                            raise PermissionError(f"Tool not selected or permitted: {call.name}")
                        payload = await self.mcp.call(
                            self.run_id,
                            role,
                            call.name,
                            call.arguments,
                            f"{task.id}:{step}:{call.id}",
                        )
                        if not payload.get("isError", False):
                            routing.used("tools", call.name)
                    else:
                        payload = None
                    if payload is not None:
                        self.store.append_event(
                            self.run_id,
                            role.name,
                            "agent.tool",
                            {"name": call.name, "arguments": call.arguments},
                        )
                        request.messages.append(
                            Message(
                                role="tool",
                                tool_call_id=call.id,
                                content=json.dumps(redact(payload)),
                            )
                        )
                        save_checkpoint(step, result.tool_calls[index + 1 :])
                        continue
                    if call.name not in tools:
                        raise PermissionError(f"Tool not allowed: {call.name}")
                    tool = tools[call.name]
                    arguments = tool.arguments.model_validate(call.arguments)
                    answer = tool.result.model_validate(await invoke(tool.handler, arguments))
                    routing.used("tools", call.name)
                    self.store.append_event(
                        self.run_id,
                        role.name,
                        "agent.tool",
                        {
                            "name": call.name,
                            "call_id": call.id,
                            "arguments": call.arguments,
                            "result": redact(answer.model_dump(mode="json")),
                        },
                    )
                    request.messages.append(
                        Message(
                            role="tool",
                            tool_call_id=call.id,
                            content=json.dumps(redact(answer.model_dump(mode="json"))),
                        )
                    )
                    save_checkpoint(step, result.tool_calls[index + 1 :])
                with self.store.connect() as db:
                    db.execute(
                        "DELETE FROM agent_checkpoints WHERE run_id=? AND task_id=?",
                        (self.run_id, task.id),
                    )
                continue
            try:
                output = schema.model_validate_json(result.content or "")
            except ValidationError:
                if retried:
                    raise
                retried = True
                request.messages.extend(
                    [
                        Message(role="assistant", content=redact(result.content or "")),
                        Message(
                            role="user", content="Invalid output schema. Return corrected JSON."
                        ),
                    ]
                )
                continue
            artifact = Artifact(
                id=uuid4().hex,
                run_id=self.run_id,
                name=f"agent/{task.id or uuid4().hex}.json",
                mime="application/json",
                data=json.dumps(redact(output.model_dump(mode="json"))).encode(),
            )
            self.store.save_artifact(artifact, role.name, "agent.completed")
            routing.outcome("completed")
            return artifact
        raise RuntimeError(f"Step cap reached: {role.name}")
