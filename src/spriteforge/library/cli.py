"""`spriteforge library ...`: ingest downloaded packs and browse the catalog."""

from pathlib import Path
from typing import Annotated

import typer

from . import store

app = typer.Typer(no_args_is_help=True, help="Third-party asset library with license tracking.")


def _cell(value: str) -> tuple[int, int] | str | None:
    if not value:
        return None
    if value == "auto":
        return "auto"
    w, _, h = value.lower().partition("x")
    return int(w), int(h or w)


@app.command()
def add(
    source: Path,
    pack: Annotated[str, typer.Option(help="Pack id, e.g. kenney-tiny-dungeon.")],
    category: Annotated[str, typer.Option(help="character/enemy/tileset/item/...")] = "other",
    subcategory: str = "",
    title: str = "",
    tag: Annotated[list[str] | None, typer.Option(help="Extra tag (repeatable).")] = None,
    cell: Annotated[str, typer.Option(help="Grid cell for sheets, e.g. 16x16.")] = "",
    author: str = "unknown",
    source_url: str = "",
    license: Annotated[str, typer.Option(help="CC0, CC-BY-4.0, ... or UNKNOWN.")] = "UNKNOWN",
    license_url: str = "",
    verified: Annotated[bool, typer.Option(help="License checked on the source page.")] = False,
    notes: str = "",
):
    """Add a downloaded .zip, folder or image to assets/library/."""
    result = store.add_pack(
        source, pack, title=title, category=category, subcategory=subcategory, tags=tag or [],
        cell=_cell(cell), author=author, source_url=source_url, license=license,
        license_url=license_url, license_verified=verified, notes=notes,
    )  # fmt: skip
    typer.echo(f"{result.id}: {len(result.assets)} assets, usage={result.usage}")
    if result.usage == "reference-only":
        typer.echo("  License not verified: builds will not ship this art. Use `library license`.")


@app.command("list")
def list_cmd():
    """Show packs with their license status."""
    for p in store.load_packs():
        typer.echo(f"{p.id:32} {len(p.assets):4} assets  {p.license:12} {p.usage}")


@app.command()
def search(category: str = "", tag: str = "", cell: str = "", usage: str = ""):
    """Find assets, e.g. --category equipment --tag chainmail --usage ship."""
    for a in store.search(category=category, tag=tag, size=_cell(cell), usage=usage):
        typer.echo(f"{a['id']:44} {a['cell'][0]}x{a['cell'][1]}  {a['usage']:16} {a['file']}")


@app.command()
def describe(
    pack: Annotated[list[str] | None, typer.Option(help="Only this pack (repeatable).")] = None,
    force: Annotated[bool, typer.Option(help="Describe again what is already described.")] = False,
    mock: Annotated[bool, typer.Option(help="Offline: measured looks only, no model.")] = False,
    max_calls: Annotated[int, typer.Option(help="Cap on Muse Spark calls.")] = 30,
    max_cost: Annotated[float, typer.Option(help="Cap on the estimated cost, USD.")] = 0.5,
    config: Path | None = None,
    workspace: Path | None = None,
):
    """Describe every library image: a measured look (colours, brightness, detail) for all,
    and a Muse Spark (vision) description for packs that allow AI use."""
    from spriteforge.config import BudgetConfig, init_workspace, load_config
    from spriteforge.core.cost import Budget
    from spriteforge.core.credentials import load_credentials
    from spriteforge.core.state import Store
    from spriteforge.providers.muse_spark import MuseSparkProvider

    from .describe import describe_library

    cfg = load_config(config)
    provider, budget = None, None
    if not mock:
        load_credentials()
        root = init_workspace(workspace or cfg.workspace)
        store = Store(root / "library.sqlite3")
        cap = BudgetConfig(**{**cfg.budget.model_dump(), "max_calls_per_model": max_calls,
                              "max_estimated_cost": max_cost})  # fmt: skip
        run = store.create_run({"workflow": "library-describe", "budget": cap.model_dump()})
        budget = Budget(store, run.id, cap)
        provider = MuseSparkProvider(cfg.llm, budget)
    counts = describe_library(provider=provider, model=cfg.llm.model if provider else "",
                              reasoning=cfg.llm.reasoning.get("describe"), packs=pack or None,
                              force=force, log=typer.echo)  # fmt: skip
    if provider is not None:
        provider.close()
    typer.echo(
        f"{counts['model']} described by the model, {counts['measured']} measured only, "
        f"{counts['kept']} unchanged, {counts['failed']} not described"
        + (f"; about ${budget.spent():.3f}" if budget else "")
    )


@app.command()
def mcp():
    """Expose read-only library tools (packs, search, actors, tile sets) over MCP stdio."""
    from spriteforge.mcp.library import create_server

    create_server().run(transport="stdio")


@app.command()
def license(
    pack: str,
    license: str,
    source_url: str = "",
    license_url: str = "",
    author: str = "",
):
    """Record a license you checked on the pack's download page (marks it verified)."""
    p = store.set_license(
        pack, license, source_url=source_url, license_url=license_url, author=author
    )
    typer.echo(f"{p.id}: license={p.license} usage={p.usage}")
