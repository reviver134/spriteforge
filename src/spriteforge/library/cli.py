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
