from pathlib import Path

import typer

from spriteforge.agents.roles import load_roles
from spriteforge.config import init_workspace, load_config

from .catalog import SkillCatalog

app = typer.Typer(no_args_is_help=True)


def catalog(workspace):
    return SkillCatalog(init_workspace(workspace or load_config().workspace))


@app.command("list")
def list_skills(workspace: Path | None = None, role: str | None = None):
    skills = catalog(workspace)
    for name, skill in (skills.available(load_roles()[role]) if role else skills.all()).items():
        typer.echo(f"{name}: {skill.header.description}")


@app.command()
def add(
    source: str,
    workspace: Path | None = None,
    role: str = "Pixel Artist",
    subdir: str = "",
    revision: str | None = None,
):
    try:
        typer.echo(catalog(workspace).add(source, role, subdir, revision))
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from None


@app.command()
def remove(name: str, workspace: Path | None = None):
    catalog(workspace).remove(name)
    typer.echo(f"Removed {name}")
