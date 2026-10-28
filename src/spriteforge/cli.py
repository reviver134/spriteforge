import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from spriteforge.app.server import create_app
from spriteforge.config import init_workspace, load_config
from spriteforge.core.state import Store

app = typer.Typer(no_args_is_help=True)


def setup(config: Path | None, workspace: Path | None):
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
    config: Path | None = None,
    workspace: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
):
    """Start a local UI; --mock creates and runs the resumable foundation demo."""
    cfg, store = setup(config, workspace)
    run = (
        store.create_run({"workflow": "mock-demo-v1", "budget": cfg.budget.model_dump()})
        if mock
        else None
    )
    serve(store, run.id if run else None, port, open_browser)


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
):
    """Resume from the database checkpoint and show its timeline."""
    _, store = setup(config, workspace)
    try:
        store.get_run(id)
    except KeyError:
        raise typer.BadParameter(f"Run {id} does not exist") from None
    serve(store, id, port, open_browser)
