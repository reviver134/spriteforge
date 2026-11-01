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
):
    """Design, art (library first, Muse Image for the rest), code, playtest and repair."""
    from .pipeline import make_game

    if not mock:
        load_credentials()
    result = make_game(prompt, out, load_config(), mock=mock, record=record, log=typer.echo)
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


@app.command("db")
def db_cmd(
    path: Annotated[
        Path, typer.Argument(help="A `game make` folder, a run folder or a db folder.")
    ],
    focus: Annotated[str, typer.Option(help="What the designer is working on.")] = "",
    budget: Annotated[int, typer.Option(help="About this many tokens.")] = 2000,
):
    """Print the game database's context pack: the canon a level or menu designer reads."""
    from spriteforge.core.state import Store

    from .gamedb import context_pack, open_db

    root = next((p for p in (path / "db", path / "game" / "db", path) if (p / "index.md").is_file()),
                None)  # fmt: skip
    if root is None:
        raise typer.BadParameter(f"no game database under {path}")
    store = Store(root.parent / "db-reads.sqlite3")
    run = store.create_run({"workflow": "game-db-read"})
    typer.echo(context_pack(open_db(root, store, run.id), focus, budget))


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
