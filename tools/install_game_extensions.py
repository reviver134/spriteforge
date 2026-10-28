"""Install the reviewed local extension snapshots; never enable or run a server."""

import json
import shutil
import sys
from pathlib import Path

from spriteforge.config import init_workspace
from spriteforge.mcp.registry import Registry, Server
from spriteforge.skills.catalog import SkillCatalog


def main():
    root = init_workspace(Path("workspace"))
    source = root / "extensions/sources/gamedev-skills"
    catalog = SkillCatalog(root)
    choices = {
        "create-game-assets": "Pixel Artist",
        "level-design": "Level/Tile Designer",
        "game-feel": "Game Designer",
    }
    for name, role in choices.items():
        if name not in catalog.all():
            catalog.add(str(source / "skills/disciplines" / name), role)
        shutil.copyfile(source / "LICENSE", root / "skills" / name / "LICENSE")
    grants = catalog.grants()
    grants.roles["Art Director"] = sorted(
        set(grants.roles.get("Art Director", []) + ["create-game-assets"])
    )
    catalog.grants_path.write_text(grants.model_dump_json(indent=2), encoding="utf-8")
    registry = Registry(root)
    definitions = {
        "pixel-tools": Server(
            command=sys.executable, args=["-m", "spriteforge.mcp.pixel_tools", str(root)]
        ),
        "filesystem-read": Server(
            command=sys.executable,
            args=["-m", "spriteforge.mcp.filesystem_read", str(root / "runs")],
        ),
        "knowledge": Server(
            command=sys.executable,
            args=["-m", "spriteforge", "memory", "mcp", "--workspace", str(root)],
        ),
    }
    for name, server in definitions.items():
        if name not in registry.read().mcpServers:
            registry.add(name, server)
    provenance = {
        "skills": {
            "repository": "https://github.com/gamedev-skills/awesome-gamedev-agent-skills",
            "commit": "19d93f97c9c1ea0f8b306e77aa81e0d3e352d3e9",
            "license": "Apache-2.0",
            "installed": choices,
        },
        "pixel-tools": {
            "source": "src/spriteforge/mcp/pixel_tools.py",
            "status": "Free local Pillow/NumPy tools; new registrations disabled until enabled per role",
            "dependencies": "Existing SpriteForge dependencies only; no editor executable or API key",
        },
    }
    (root / "extensions/sources.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    print("Installed skills:", ", ".join(choices))
    print("Servers registered disabled:", ", ".join(definitions))


if __name__ == "__main__":
    main()
