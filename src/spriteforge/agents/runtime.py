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

from spriteforge.core.security import redact
from spriteforge.core.state import Artifact, Store
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.providers.base import ChatRequest, LLMProvider, Message

from .cancellation import cancelled, check_active
from .roles import Role


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
    ):
        self.store, self.run_id, self.memory, self.provider = store, run_id, memory, provider
        self.tools, self.skills = tools or {}, skills or {}

    async def run(
        self, role: Role, task: AgentTask, schema: type[BaseModel], *, timeout: float = 60
    ) -> Artifact:
        signal = threading.Event()
        token = cancelled.set(signal)
        try:
            return await asyncio.wait_for(self._loop(role, task, schema), timeout)
        except Exception as exc:
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
        missing_skills = set(role.allowed_skills) - self.skills.keys()
        if missing_skills:
            raise ValueError(f"Unavailable skills: {sorted(missing_skills)}")
        tools = {name: self.tools[name] for name in role.allowed_tools if name in self.tools}
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
                                "skills": {n: self.skills[n] for n in role.allowed_skills},
                            }
                        )
                    ),
                ),
            ],
            tools=[tool.spec(name) for name, tool in tools.items()],
            json_schema=schema.model_json_schema(),
        )
        provider = self.provider[role.model] if isinstance(self.provider, dict) else self.provider
        retried = False
        for step in range(role.max_steps):
            result = await invoke(provider.chat, request)
            self.store.append_event(
                self.run_id, role.name, "agent.step", {"step": step + 1, "model": role.model}
            )
            if result.tool_calls:
                request.messages.append(Message(role="assistant", tool_calls=result.tool_calls))
                for raw in result.tool_calls:
                    # Accept the provider-neutral and OpenAI-compatible tool-call shapes.
                    if "function" in raw:
                        raw = {
                            "id": raw["id"],
                            "name": raw["function"]["name"],
                            "arguments": json.loads(raw["function"]["arguments"]),
                        }
                    call = ToolCall.model_validate(raw)
                    if call.name not in tools:
                        raise PermissionError(f"Tool not allowed: {call.name}")
                    tool = tools[call.name]
                    arguments = tool.arguments.model_validate(call.arguments)
                    answer = tool.result.model_validate(await invoke(tool.handler, arguments))
                    self.store.append_event(
                        self.run_id,
                        role.name,
                        "agent.tool",
                        {
                            "name": call.name,
                            "call_id": call.id,
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
            return artifact
        raise RuntimeError(f"Step cap reached: {role.name}")
