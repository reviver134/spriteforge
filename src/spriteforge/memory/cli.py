from pathlib import Path

import typer

from spriteforge.core.credentials import load_credentials

from .okf.seed import seed
from .okf.store import KnowledgeStore
from .okf.validate import validate as validate_corpus

app = typer.Typer(no_args_is_help=True, help="Local OKF knowledge and retrieval.")


def open_store(workspace: Path) -> KnowledgeStore:
    load_credentials()
    store = KnowledgeStore(workspace / "memory")
    if not store.scan()[1]:
        seed(store)
    return store


@app.command()
def init(workspace: Path = Path("workspace")):
    """Initialize the local Git corpus and seed library without overwriting entries."""
    store = open_store(workspace)
    typer.echo(f"Knowledge corpus: {store.root}")


@app.command()
def search(
    query: str,
    workspace: Path = Path("workspace"),
    scope: list[str] | None = None,
    token_budget: int = 1024,
    min_score: float | None = None,
    relative_score: float | None = None,
    verbose: bool = False,
):
    """Return ranked snippets with trust tiers and path-derived IDs."""
    store = open_store(workspace)
    for snippet in store.retrieve(
        query, scope, token_budget, min_score=min_score, relative_score=relative_score
    ):
        flags = "; bootstrap, unreviewed" if snippet.bootstrap_unreviewed else ""
        flags += " STALE" if snippet.stale else ""
        if snippet.status != "stable":
            flags += " " + snippet.status.upper()
        if verbose:
            flags += f" score={snippet.score:.3f}"
        typer.echo(f"[{snippet.trust_tier}{flags}] {snippet.id}: {snippet.title}\n{snippet.text}\n")


@app.command("validate")
def validate_command(workspace: Path = Path("workspace")):
    """Validate local OKF and run the optional external `okf validate` tool."""
    report = validate_corpus(open_store(workspace))
    for issue in report.errors:
        typer.echo(f"ERROR: {issue}")
    for issue in report.warnings:
        typer.echo(f"WARNING: {issue}")
    typer.echo(f"External okf: {report.external}")
    typer.echo("PASS" if not report.errors else "FAIL")
    if report.errors:
        raise typer.Exit(1)


@app.command()
def index(directory: str = "", workspace: Path = Path("workspace"), scope: list[str] | None = None):
    """List summaries before selecting a full entry."""
    typer.echo(open_store(workspace).index(directory, scope))


@app.command()
def show(id: str, workspace: Path = Path("workspace"), scope: list[str] | None = None):
    """Load one full concept after search/index discovery."""
    from .okf.codec import serialize

    typer.echo(serialize(open_store(workspace).show(id, scope)))


@app.command()
def promote(id: str, by: str, workspace: Path = Path("workspace")):
    """Explicit human sign-off; agent edits subsequently clear this verification."""
    open_store(workspace).promote(id, by if by.startswith("human:") else "human:" + by)
    typer.echo(f"Human verification recorded for {id}")


@app.command()
def export(destination: Path, workspace: Path = Path("workspace")):
    """Export public concepts and fresh indexes, excluding private data and Git history."""
    open_store(workspace).export_public(destination)
    typer.echo(f"Public knowledge exported to {destination}")


@app.command()
def mcp(workspace: Path = Path("workspace"), allow_private: bool = False):
    """Expose read-only corpus tools over MCP stdio; stdout is protocol-only."""
    from spriteforge.mcp.memory import create_server

    create_server(open_store(workspace), allow_private=allow_private).run(transport="stdio")


@app.command()
def review(
    id: str | None = typer.Argument(None),
    confirm: bool = False,
    workspace: Path = Path("workspace"),
):
    """List bootstrap entries awaiting review, or explicitly confirm one."""
    store = open_store(workspace)
    if id:
        if not confirm:
            raise typer.BadParameter("Review requires --confirm")
        store.review(id)
        typer.echo(f"Review recorded for {id}")
    else:
        for entry in store.scan()[0]:
            if entry.metadata.model_extra.get("x-spriteforge-review") == "unreviewed":
                typer.echo(f"{entry.id}: {entry.title} [bootstrap, unreviewed]")
