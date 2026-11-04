import asyncio
from pathlib import Path

import typer

from spriteforge.config import init_workspace, load_config
from spriteforge.core.state import Store

from .registry import Registry, Server
from .service import MCPService

app = typer.Typer(no_args_is_help=True)


def registry(workspace):
    return Registry(init_workspace(workspace or load_config().workspace))


@app.command("list")
def list_servers(workspace: Path | None = None):
    for name, server in registry(workspace).read().mcpServers.items():
        typer.echo(
            f"{name}: {server.transport}; enabled roles={list(server.enabled_roles)}; tools={[t['name'] for t in server.tools]}"
        )


@app.command()
def add(name: str, config: Path, workspace: Path | None = None):
    registry(workspace).add(name, Server.model_validate_json(config.read_text(encoding="utf-8")))
    typer.echo(f"Added {name}, disabled. Inspect its tools in the UI before enabling.")


@app.command()
def inspect(name: str, workspace: Path | None = None):
    reg = registry(workspace)
    service = MCPService(Store(reg.workspace / "state.sqlite3"))
    typer.echo(asyncio.run(service.discover(name)))


@app.command()
def enable(server: str, role: str = typer.Option(...), workspace: Path | None = None):
    registry(workspace).enable(server, role)
    typer.echo(f"Enabled {server} for {role}")


@app.command()
def policy(server: str, tool: str, confirm: bool = typer.Option(True, "--confirm/--no-confirm"),
           workspace: Path | None = None):  # fmt: skip
    """Whether a tool asks you before each call. Agents of a running build only use tools
    that do not ask (a build cannot stop half way to ask)."""
    registry(workspace).policy(server, tool, confirm)
    typer.echo(f"{server}.{tool}: " + ("asks before each call" if confirm else "runs without asking"))


@app.command()
def disable(server: str, role: str | None = None, workspace: Path | None = None):
    registry(workspace).disable(server, role)
    typer.echo(f"Disabled {server}")
