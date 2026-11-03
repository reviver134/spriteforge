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
from spriteforge.game.cli import app as game_app
from spriteforge.library.cli import app as library_app
from spriteforge.mcp.cli import app as mcp_app
from spriteforge.memory.cli import app as memory_app
from spriteforge.skills.cli import app as skills_app

app = typer.Typer()
app.add_typer(memory_app, name="memory")
app.add_typer(skills_app, name="skills")
app.add_typer(mcp_app, name="mcp")
app.add_typer(library_app, name="library")
app.add_typer(game_app, name="game")


def setup(config: Path | None, workspace: Path | None):
    load_credentials()
    cfg = load_config(config)
    root = init_workspace(workspace or cfg.workspace)
    return cfg, Store(root / "state.sqlite3")


def serve(store, run_id, port, open_browser):
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"SpriteForge Studio · {url}" + (f" · run {run_id}" if run_id else ""))
    if open_browser:
        timer = threading.Timer(1.5, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(store, run_id), host="127.0.0.1", port=port, access_log=False)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """SpriteForge. With no command, opens the studio in the browser."""
    if ctx.invoked_subcommand is None:
        studio()


@app.command()
def studio(
    config: Path | None = None,
    workspace: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
):
    """Open the studio: every project in the workspace, chat-first (the default command)."""
    _, store = setup(config, workspace)
    serve(store, None, port, open_browser)


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
            store,
            goal or "A top-down dungeon crawler with a hero and stone rooms",
            cfg.budget,
            router=cfg.router,
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
def router_eval(
    live: bool = False,
    max_calls: int = typer.Option(5, min=1, max=20),
    offset: int = typer.Option(0, min=0),
    cases: Path = Path("tests/router_cases.yaml"),
    config: Path | None = None,
    workspace: Path | None = None,
):
    """Report router precision/recall; --live makes at most --max-calls Spark requests."""
    from uuid import uuid4

    from spriteforge.agents.router_eval import evaluate, read_cases
    from spriteforge.core.cost import Budget
    from spriteforge.core.state import Artifact
    from spriteforge.providers.mock import MockLLMProvider
    from spriteforge.providers.muse_spark import MuseSparkProvider

    cfg, store = setup(config, workspace)
    suite = read_cases(cases)[offset:]
    limits = (
        cfg.budget.model_copy(
            update={
                "max_total_calls": min(
                    cfg.budget.max_total_calls
                    if cfg.budget.max_total_calls is not None
                    else max_calls,
                    max_calls,
                )
            }
        )
        if live
        else cfg.budget
    )
    run = store.create_run(
        {
            "workflow": "router-eval",
            "budget": limits.model_dump(),
            "settings": cfg.model_dump(mode="json"),
        }
    )
    budget = Budget(store, run.id, limits)
    provider = MuseSparkProvider(cfg.llm, budget) if live else MockLLMProvider(budget)
    try:
        report = evaluate(
            provider, suite, cfg.router, store.path.parent, live=live, max_calls=max_calls
        )
    finally:
        if live:
            provider.close()
    store.save_artifact(
        Artifact(
            id=uuid4().hex,
            run_id=run.id,
            name="router-eval.json",
            mime="application/json",
            data=report.model_dump_json(indent=2).encode(),
        ),
        "Router",
        "router.eval",
    )
    with store.connect() as db:
        db.execute("UPDATE runs SET status='completed' WHERE id=?", (run.id,))
    typer.echo(f"{report.mode}: {report.evaluated} cases, {report.skipped} skipped")
    typer.echo(f"Spark calls attempted: {report.evaluated if live else 0} (cap {max_calls})")
    typer.echo(f"Precision: {report.precision:.3f}  Recall: {report.recall:.3f}")
    typer.echo(f"Failures: {len(report.failures)}")
    for failure in report.failures:
        typer.echo(failure.model_dump_json())
    typer.echo(f"Run: {run.id} (router-eval.json contains all results)")


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
