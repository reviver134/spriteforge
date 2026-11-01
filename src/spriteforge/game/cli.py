"""`spriteforge game ...`: make a game from a prompt (no gates) or re-run a build's playtest."""

import json
from pathlib import Path
from typing import Annotated

import typer

from spriteforge.config import load_config
from spriteforge.core.credentials import load_credentials

app = typer.Typer(no_args_is_help=True, help="Games written by Muse Spark from a prompt.")


@app.command()
def make(
    prompt: str,
    out: Annotated[Path, typer.Option(help="Output folder (replaced).")],
    mock: Annotated[bool, typer.Option(help="Offline stand-ins; no model calls.")] = False,
    record: Annotated[bool, typer.Option(help="Record the autoplay (poster/video).")] = True,
    story: Annotated[
        bool, typer.Option(help="Write a story: named characters, a world and an arc.")
    ] = False,
):
    """Design, art (library first, Muse Image for the rest), code, playtest and repair."""
    from .pipeline import make_game

    if not mock:
        load_credentials()
    result = make_game(prompt, out, load_config(), mock=mock, record=record, log=typer.echo,
                       story=story)  # fmt: skip
    r = result.report
    typer.echo(
        f"\n{r['title']}: {'PASSED' if r['passed'] else 'FAILED'} after "
        f"{len(r['attempts'])} attempt(s); calls {r['model_calls']}"
    )
    for w in r["warnings"]:
        typer.echo(f"  warning: {w}")
    if r["play"]:
        typer.echo(f"Play: {r['play']}")
    typer.echo(f"Report: {out / 'report.json'}")
    if not r["passed"]:
        raise typer.Exit(1)


db_app = typer.Typer(no_args_is_help=True, help="A game's record: its OKF bundle.")
app.add_typer(db_app, name="db")
PathArg = Annotated[Path, typer.Argument(help="A `game make` folder, a run folder or a bundle.")]


@db_app.command("context")
def db_context(
    path: PathArg,
    focus: Annotated[str, typer.Option(help="What the designer is working on.")] = "",
    budget: Annotated[int, typer.Option(help="About this many tokens.")] = 2000,
):
    """Print the context pack a level or menu designer reads (from the bundle, not the code)."""
    from spriteforge.core.state import Store

    from .gamedb import context_pack, find_db, open_db

    try:
        root = find_db(path)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    store = Store(root.parent / "db-reads.sqlite3")
    run = store.create_run({"workflow": "game-db-read"})
    typer.echo(context_pack(open_db(root, store, run.id), focus, budget))


@db_app.command("mcp")
def db_mcp(path: PathArg):
    """Serve read-only tools over the bundle (game_index, game_search, game_show,
    game_context) on MCP stdio."""
    from spriteforge.mcp.gamedb import create_server

    create_server(path).run(transport="stdio")


@db_app.command("sync")
def db_sync(
    path: Annotated[Path, typer.Argument(help="A run folder (runs/<id>) or a `game make` folder.")],
):
    """Rebuild a game's bundle from a run's artifacts or a `game make` folder (old runs,
    repairs). Only what differs is written; concepts no longer wanted are deprecated."""
    from spriteforge.core.state import Store

    from .dbsource import folder_record
    from .gamedb import open_db, sync_record

    path = path.resolve()
    if (path / "run.sqlite3").is_file() and (path / "design.json").is_file():
        store = Store(path / "run.sqlite3")
        runs = store.runs()
        run_id = runs[0].id if runs else store.create_run({"workflow": "game-db-sync"}).id
        db = open_db(path / "db", store, run_id)
        counts = sync_record(db, folder_record(path), "Game record: rebuilt by game db sync")
    elif (path.parent.parent / "state.sqlite3").is_file() and path.parent.name == "runs":
        from spriteforge.stages.game import GameContext, sync_db

        store = Store(path.parent.parent / "state.sqlite3")
        counts = sync_db(GameContext(store, path.name), "rebuilt by game db sync")
    else:
        raise typer.BadParameter(f"{path} is neither a run folder nor a `game make` folder")
    typer.echo(f"{counts['written']} concept(s) and {counts['files']} file(s) written; "
               f"{counts['entries']} concepts wanted")  # fmt: skip


@app.command("playtest")
def playtest_cmd(out: Path):
    """Re-run the playtest of a `game make` folder (uses its design.json and build/)."""
    from .design import GameDesign
    from .playtest import playtest

    design = GameDesign.model_validate_json((out / "design.json").read_text(encoding="utf-8"))
    report = playtest(out / "build", design.checks, design.session_seconds)
    typer.echo(json.dumps({k: report[k] for k in ("passed", "ended", "game_seconds", "errors")}))
    for c in report["checks"]:
        typer.echo(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['event']}: {c['count']}/{c['min_count']}")
