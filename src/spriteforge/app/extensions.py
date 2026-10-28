"""Human-only extension administration and per-call confirmation routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from spriteforge.agents.roles import load_roles
from spriteforge.core.security import redact
from spriteforge.mcp.service import ConfirmationRequired, MCPService
from spriteforge.skills.catalog import SkillCatalog


class EnableRequest(BaseModel):
    role: str
    enabled: bool


class PolicyRequest(BaseModel):
    tool: str
    confirm: bool


class InvokeRequest(BaseModel):
    role: str
    tool: str
    arguments: dict = Field(default_factory=dict)


class Decision(BaseModel):
    approve: bool


def router(store, service=None):
    def local_request(request: Request):
        # Explicit UI approvals must not be forgeable by an unrelated web page.
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            raise HTTPException(403, "Local host required")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Same-origin request required")

    api = APIRouter(dependencies=[Depends(local_request)])
    service = service or MCPService(store)
    roles = load_roles()

    @api.get("/api/extensions")
    def extensions():
        return {
            "roles": list(roles),
            "skills": {n: SkillCatalog(store.path.parent).summaries(r) for n, r in roles.items()},
            "servers": {
                n: redact(s.model_dump()) for n, s in service.registry.read().mcpServers.items()
            },
        }

    @api.post("/api/mcp/{name}/inspect")
    async def inspect(name: str):
        try:
            return {"tools": await service.discover(name)}
        except Exception as exc:  # noqa: BLE001 -- scrub all SDK errors at the HTTP boundary
            raise HTTPException(400, redact(str(exc))) from None

    @api.post("/api/mcp/{name}/enable")
    def enable(name: str, body: EnableRequest):
        try:
            if body.enabled:
                service.registry.enable(name, body.role)
            else:
                service.registry.disable(name, body.role)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    @api.post("/api/mcp/{name}/policy")
    def policy(name: str, body: PolicyRequest):
        try:
            service.registry.policy(name, body.tool, body.confirm)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    @api.get("/api/runs/{run_id}/mcp/confirmations")
    def confirmations(run_id: str):
        return service.pending(run_id)

    @api.post("/api/runs/{run_id}/mcp/call")
    async def call(run_id: str, body: InvokeRequest):
        try:
            return await service.call(run_id, roles[body.role], body.tool, body.arguments)
        except ConfirmationRequired as exc:
            return {"confirmation": exc.ident}
        except Exception as exc:  # noqa: BLE001 -- scrub all SDK errors at the HTTP boundary
            raise HTTPException(400, redact(str(exc))) from None

    @api.post("/api/runs/{run_id}/mcp/confirmations/{ident}")
    async def confirm(run_id: str, ident: str, body: Decision):
        try:
            row = service.row(run_id, ident)
            if row is None:
                raise ValueError("Unknown confirmation")
            import json

            role = roles[json.loads(row["intent"])["role"]]
            result = await service.respond(run_id, ident, body.approve, role)
            # Runtime checkpoints consume the saved result without repeating the tool call.
            if store.get_run(run_id).config.get("workflow") == "agent-runtime-mock-v1":
                import asyncio

                from spriteforge.agents.workflow import advance_until_gate

                await asyncio.to_thread(advance_until_gate, store, run_id)
            return result
        except Exception as exc:  # noqa: BLE001 -- scrub all SDK errors at the HTTP boundary
            raise HTTPException(409, redact(str(exc))) from None

    return api
