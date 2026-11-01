from pathlib import PurePosixPath

from .models import Entry


def indexes(
    entries: list[Entry],
    directories: set[str] | None = None,
    *,
    absolute: bool = False,
    title: str = "SpriteForge knowledge",
) -> dict[str, str]:
    """Every directory's index.md. `absolute`: links are bundle-relative from the root
    (`/entities/hero.md`), as OKF recommends; otherwise relative to the index."""
    dirs = {"", *(directories or set())}
    for entry in entries:
        parent = PurePosixPath(entry.id).parent
        dirs.update(str(p) if str(p) != "." else "" for p in [parent, *parent.parents])
    files = {}
    for directory in sorted(dirs):
        prefix = directory + "/" if directory else ""
        lines = ['---\nokf_version: "0.2"\n---\n'] if not directory else []
        lines.append(f"# {directory or title}\n")
        for child in sorted(
            d
            for d in dirs
            if d
            and ("" if str(PurePosixPath(d).parent) == "." else str(PurePosixPath(d).parent))
            == directory
        ):
            name = child.rsplit("/", 1)[-1]
            target = f"/{child}/index.md" if absolute else f"{name}/index.md"
            lines.append(f"* [{name}]({target}) - Browse {name} summaries.\n")
        for entry in sorted(entries, key=lambda e: e.id):
            if str(PurePosixPath(entry.id).parent) not in {
                directory,
                "." if not directory else directory,
            }:
                continue
            # Public indexes never expose private concept titles or descriptions.
            if entry.private and not directory.startswith("user-prefs"):
                continue
            title = entry.title.replace("\n", " ").replace("[", "").replace("]", "")
            summary = (entry.metadata.description or "Open this concept for details.").replace(
                "\n", " "
            )
            target = f"/{entry.id}.md" if absolute else f"{entry.id.rsplit('/', 1)[-1]}.md"
            lines.append(f"* [{title}]({target}) - {summary}\n")
        files[prefix + "index.md"] = "".join(lines)
    return files
