"""User-owned server configuration. Add is always disabled; discovery is explicit."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spriteforge.core.security import redact
from spriteforge.memory.okf.git import write_lock


class Server(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: Literal["stdio", "http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)  # child variable -> host variable NAME
    headers_env: dict[str, str] = Field(default_factory=dict)
    enabled_roles: dict[str, list[str]] = Field(default_factory=dict)
    confirmation: dict[str, bool] = Field(default_factory=dict)
    tools: list[dict] = Field(default_factory=list)
    discovered_fingerprint: str | None = None

    @model_validator(mode="after")
    def valid_transport(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio requires command")
        if self.transport == "http":
            from urllib.parse import urlsplit

            url = urlsplit(self.url or "")
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("HTTP requires a credential-free http(s) URL")
        return self

    def fingerprint(self):
        connection = self.model_dump(
            exclude={"enabled_roles", "confirmation", "tools", "discovered_fingerprint"}
        )
        return hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest()


class Servers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mcpServers: dict[str, Server] = Field(default_factory=dict)


class Registry:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.path = self.workspace / "mcp.json"

    def read(self):
        return (
            Servers.model_validate_json(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else Servers()
        )

    def write(self, config):
        raw = config.model_dump_json(indent=2)
        if redact(raw) != raw:
            raise ValueError("Credentials belong in environment variables, not MCP configuration")
        pending = self.path.with_suffix(".json.tmp")
        pending.write_text(raw, encoding="utf-8")
        pending.replace(self.path)

    def add(self, name, server):
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in name):
            raise ValueError("Server names use lowercase letters, digits, hyphens or underscores")
        with write_lock(self.workspace / ".mcp-config.lock"):
            config = self.read()
            if name in config.mcpServers:
                raise ValueError("Server already exists")
            server = server.model_copy(deep=True)
            server.enabled_roles, server.tools, server.confirmation = {}, [], {}
            server.discovered_fingerprint = None
            config.mcpServers[name] = server
            self.write(config)

    def discovered(self, name, fingerprint, tools):
        with write_lock(self.workspace / ".mcp-config.lock"):
            config = self.read()
            server = config.mcpServers[name]
            if fingerprint != server.fingerprint():
                raise ValueError("Server changed during discovery")
            # Changed definitions revoke grants until the human reviews them again.
            if server.tools != tools:
                server.enabled_roles = {}
                server.confirmation = {}
            server.tools = tools
            server.discovered_fingerprint = fingerprint
            self.write(config)

    def enable(self, name, role):
        from spriteforge.agents.roles import load_roles

        if role not in load_roles():
            raise ValueError("Unknown role")
        with write_lock(self.workspace / ".mcp-config.lock"):
            config = self.read()
            server = config.mcpServers[name]
            if server.discovered_fingerprint != server.fingerprint():
                raise ValueError("Inspect server tools before enabling")
            server.enabled_roles[role] = [t["name"] for t in server.tools]
            self.write(config)

    def disable(self, name, role=None):
        with write_lock(self.workspace / ".mcp-config.lock"):
            config = self.read()
            if role:
                config.mcpServers[name].enabled_roles.pop(role, None)
            else:
                config.mcpServers[name].enabled_roles = {}
            self.write(config)

    def policy(self, name, tool, confirm):
        with write_lock(self.workspace / ".mcp-config.lock"):
            config = self.read()
            server = config.mcpServers[name]
            if tool not in {t["name"] for t in server.tools}:
                raise ValueError("Unknown tool")
            server.confirmation[tool] = confirm
            self.write(config)

    def allowed(self, role):
        result = {}
        for name, server in self.read().mcpServers.items():
            if server.discovered_fingerprint != server.fingerprint():
                continue
            for tool in server.tools:
                qualified = name + "." + tool["name"]
                if tool["name"] in server.enabled_roles.get(role.name, []):
                    result[qualified] = (server, tool)
        return result
