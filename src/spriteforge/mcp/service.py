"""Durable one-shot tool confirmations and role-enforced invocation."""

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, Field

from spriteforge.agents.cancellation import check_active
from spriteforge.core.security import redact

from .client import MCPClient
from .registry import Registry


class ConfirmationRequired(Exception):
    def __init__(self, ident):
        self.ident = ident
        super().__init__(f"Human confirmation required: {ident}")


class CallIntent(BaseModel):
    id: str
    role: str
    tool: str
    arguments: dict = Field(default_factory=dict)
    fingerprint: str
    prior_status: str


class MCPService:
    def __init__(self, store, client=None):
        self.store = store
        self.registry = Registry(store.path.parent)
        self.client = client or MCPClient()
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS mcp_calls (
                run_id TEXT, id TEXT, intent TEXT, status TEXT, result TEXT,
                PRIMARY KEY(run_id,id))""")

    async def discover(self, name):
        server = self.registry.read().mcpServers[name]
        tools = await self.client.discover(server)
        self.registry.discovered(name, server.fingerprint(), tools)
        return tools

    def pending(self, run_id):
        with self.store.connect() as db:
            return [
                {**json.loads(r["intent"]), "status": r["status"]}
                for r in db.execute(
                    "SELECT intent,status FROM mcp_calls WHERE run_id=? AND status='pending'",
                    (run_id,),
                )
            ]

    def row(self, run_id, ident):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM mcp_calls WHERE run_id=? AND id=?", (run_id, ident)
            ).fetchone()
        return dict(row) if row else None

    def authorize(self, role, tool):
        allowed = self.registry.allowed(role)
        if tool not in allowed:
            raise PermissionError(f"MCP tool not enabled for {role.name}: {tool}")
        return allowed[tool]

    async def call(self, run_id, role, tool, arguments, ident=None):
        ident = ident or uuid4().hex
        check_active()
        self.store.append_event(
            run_id, role.name, "mcp.attempted", {"id": ident, "tool": tool, "arguments": arguments}
        )
        try:
            server, definition = self.authorize(role, tool)
        except PermissionError:
            self.store.append_event(
                run_id, role.name, "mcp.denied", {"tool": tool, "arguments": arguments}
            )
            raise
        # Persist only secret-free intent. Credentials are supplied via server env/header bindings.
        if redact(arguments) != arguments:
            raise ValueError("MCP arguments contain secrets; use server environment bindings")
        from jsonschema import Draft202012Validator
        from referencing import Registry as SchemaRegistry

        Draft202012Validator(definition.get("inputSchema", {}), registry=SchemaRegistry()).validate(
            arguments
        )
        fingerprint = hashlib.sha256(
            (server.fingerprint() + json.dumps(definition, sort_keys=True)).encode()
        ).hexdigest()
        row = self.row(run_id, ident)
        if row:
            intent = CallIntent.model_validate_json(row["intent"])
            if (intent.role, intent.tool, intent.arguments, intent.fingerprint) != (
                role.name,
                tool,
                arguments,
                fingerprint,
            ):
                raise PermissionError("MCP call changed since confirmation")
            if row["status"] == "succeeded":
                return json.loads(row["result"])
            if row["status"] == "denied":
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": "Human denied this tool call."}],
                }
            if row["status"] == "pending":
                raise ConfirmationRequired(ident)
            raise PermissionError(f"MCP call cannot replay from {row['status']}")
        run = self.store.get_run(run_id)
        if run.status in {"completed", "aborted", "failed"}:
            raise ValueError("Run is not active")
        intent = CallIntent(
            id=ident,
            role=role.name,
            tool=tool,
            arguments=arguments,
            fingerprint=fingerprint,
            prior_status=run.status,
        )
        confirm = server.confirmation.get(definition["name"], True)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO mcp_calls VALUES(?,?,?,?,NULL)",
                (run_id, ident, intent.model_dump_json(), "pending" if confirm else "approved"),
            )
            self.store._event(db, run_id, role.name, "mcp.requested", intent.model_dump())
            if confirm:
                db.execute("UPDATE runs SET status='paused' WHERE id=?", (run_id,))
        if confirm:
            raise ConfirmationRequired(ident)
        return await self.execute(run_id, ident, role)

    async def respond(self, run_id, ident, approve, role):
        with self.store.connect() as db:
            changed = db.execute(
                "UPDATE mcp_calls SET status=? WHERE run_id=? AND id=? AND status='pending'",
                ("approved" if approve else "denied", run_id, ident),
            )
            if not changed.rowcount:
                raise ValueError("Confirmation already answered or missing")
            self.store._event(
                db, run_id, "Human", "mcp.confirmed" if approve else "mcp.rejected", {"id": ident}
            )
        if not approve:
            self.restore(run_id, ident)
            return {"denied": True}
        try:
            return await self.execute(run_id, ident, role)
        finally:
            self.restore(run_id, ident)

    def restore(self, run_id, ident):
        intent = CallIntent.model_validate_json(self.row(run_id, ident)["intent"])
        if not self.pending(run_id):
            with self.store.connect() as db:
                db.execute(
                    "UPDATE runs SET status=? WHERE id=? AND status='paused'",
                    (intent.prior_status, run_id),
                )

    async def execute(self, run_id, ident, role):
        if self.store.get_run(run_id).status in {"completed", "aborted", "failed"}:
            raise PermissionError("Run is no longer active")
        intent = CallIntent.model_validate_json(self.row(run_id, ident)["intent"])
        server, definition = self.authorize(role, intent.tool)
        fingerprint = hashlib.sha256(
            (server.fingerprint() + json.dumps(definition, sort_keys=True)).encode()
        ).hexdigest()
        if intent.role != role.name or intent.fingerprint != fingerprint:
            raise PermissionError("Server, role or tool changed after confirmation")
        check_active()
        with self.store.connect() as db:
            changed = db.execute(
                "UPDATE mcp_calls SET status='running' WHERE run_id=? AND id=? AND status='approved'",
                (run_id, ident),
            )
            if not changed.rowcount:
                raise PermissionError("Confirmation already consumed")
            self.store._event(
                db,
                run_id,
                role.name,
                "mcp.called",
                {"id": ident, "tool": intent.tool, "arguments": intent.arguments},
            )
        try:
            from spriteforge.config import BudgetConfig
            from spriteforge.core.cost import Budget

            config = BudgetConfig.model_validate(
                self.store.get_run(run_id).config.get("budget", {})
            )
            with Budget(self.store, run_id, config).attempt("mcp", intent.tool.split(".", 1)[0]):
                result = redact(
                    await self.client.call(server, definition["name"], intent.arguments)
                )
        except BaseException as exc:
            with self.store.connect() as db:
                db.execute(
                    "UPDATE mcp_calls SET status='failed' WHERE run_id=? AND id=?", (run_id, ident)
                )
                self.store._event(
                    db, run_id, role.name, "mcp.failed", {"id": ident, "error": type(exc).__name__}
                )
            raise
        with self.store.connect() as db:
            db.execute(
                "UPDATE mcp_calls SET status='succeeded',result=? WHERE run_id=? AND id=?",
                (json.dumps(result), run_id, ident),
            )
            self.store._event(
                db,
                run_id,
                role.name,
                "mcp.completed",
                {"id": ident, "tool": intent.tool, "isError": result.get("isError", False)},
            )
        return result
