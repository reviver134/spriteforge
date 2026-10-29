import json
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from spriteforge.app.server import create_app
from spriteforge.config import init_workspace, load_config
from spriteforge.core.credentials import load_credentials
from spriteforge.core.state import Store
from spriteforge.mcp.cli import app as mcp_app
from spriteforge.memory.cli import app as memory_app
from spriteforge.skills.cli import app as skills_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(memory_app, name="memory")
app.add_typer(skills_app, name="skills")
app.add_typer(mcp_app, name="mcp")


def setup(config: Path | None, workspace: Path | None):
    load_credentials()
    cfg = load_config(config)
    root = init_workspace(workspace or cfg.workspace)
    return cfg, Store(root / "state.sqlite3")


def serve(store, run_id, port, open_browser):
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Run {run_id or 'viewer'} · {url}")
    if open_browser:
        timer = threading.Timer(1.5, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(store, run_id), host="127.0.0.1", port=port, access_log=False)


@app.command()
def start(
    mock: bool = False,
    agents: bool = False,
    intake: bool = False,
    assets: bool = False,
    legacy: bool = False,
    goal: str = "",
    config: Path | None = None,
    workspace: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
):
    """Start a local UI; --agents uses the Phaser gameplay preview and human gates."""
    cfg, store = setup(config, workspace)
    if legacy:
        typer.echo("DEPRECATED: --legacy enables the old concept/demo preview.", err=True)
    if intake or assets or (agents and not legacy):
        from spriteforge.stages.workflow import start_run

        run = start_run(
            store,
            cfg,
            mock=mock,
            assets=assets,
            gameplay_preview=not legacy,
            legacy=legacy,
            goal=goal,
        )
        serve(store, run.id, port, open_browser)
        return
    if agents and not mock:
        raise typer.BadParameter("The agent acceptance workflow requires --mock")
    if agents:
        from spriteforge.agents.workflow import start_run

        run = start_run(
            store, goal or "A top-down dungeon crawler with a hero and stone rooms", cfg.budget
        )
        store.append_event(run.id, "Director", "preview.deprecated", {"legacy": True})
        serve(store, run.id, port, open_browser)
        return
    run = (
        store.create_run({"workflow": "mock-demo-v1", "budget": cfg.budget.model_dump()})
        if mock
        else None
    )
    serve(store, run.id if run else None, port, open_browser)


@app.command()
def report(id: str, config: Path | None = None, workspace: Path | None = None):
    """Report successful image/Spark usage and failed attempts from the durable ledger."""
    from spriteforge.core.usage import usage_report

    _, store = setup(config, workspace)
    typer.echo(usage_report(store, id).model_dump_json(indent=2))


@app.command()
def runs(config: Path | None = None, workspace: Path | None = None):
    """List runs persisted in the workspace database."""
    _, store = setup(config, workspace)
    for run in store.runs():
        typer.echo(f"{run.id}  {run.status:10}  checkpoint={run.cursor}")


@app.command()
def resume(
    id: str,
    config: Path | None = None,
    workspace: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
    assets: bool = False,
    legacy: bool = False,
):
    """Resume from the database checkpoint and show its timeline."""
    _, store = setup(config, workspace)
    try:
        run = store.get_run(id)
    except KeyError:
        raise typer.BadParameter(f"Run {id} does not exist") from None
    old_preview = run.config.get("workflow") == "agent-runtime-mock-v1" or (
        run.config.get("workflow") == "intake-concept-v1" and not run.config.get("gameplay_preview")
    )
    if old_preview:
        if not legacy:
            raise typer.BadParameter("This deprecated preview requires explicit --legacy")
        typer.echo("DEPRECATED: --legacy enables the old concept/demo preview.", err=True)
        with store.connect() as db:
            db.execute(
                "UPDATE runs SET config=? WHERE id=?",
                (json.dumps({**run.config, "legacy_preview": True}), id),
            )
            store._event(db, id, "Director", "preview.deprecated", {"legacy": True})
    if assets:
        from spriteforge.stages.production import enable_production

        enable_production(store, id)
    if run.status == "failed" and run.config.get("workflow") == "intake-concept-v1":
        store.retry_failed(id)
    serve(store, id, port, open_browser)
