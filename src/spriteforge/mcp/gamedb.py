"""Read-only tools over one game's OKF bundle: how agents read a game's record.

Run with `spriteforge game db mcp <folder>` (stdio). Progressive disclosure, as the OKF Agent
Memory Convention asks: `game_index` (what is there), `game_search` (snippets for a query),
`game_show` (one concept), `game_context` (a budgeted pack for a design task). Never the whole
bundle, never the code files: the code is described by its `code/modules/*` concepts.

Grant it per role like any MCP server: a config file `{"transport": "stdio", "command":
"spriteforge", "args": ["game", "db", "mcp", "<folder>"]}`, `spriteforge mcp add game-db
<file>`, then `spriteforge mcp enable game-db --role "Level/Tile Designer"`.
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from spriteforge.core.state import Store
from spriteforge.game.gamedb import context_pack, find_db, open_db
from spriteforge.memory.okf.codec import serialize


def create_server(folder: Path) -> FastMCP:
    root = find_db(Path(folder))
    events = Store(root.parent / "db-reads.sqlite3")
    run = events.create_run({"workflow": "game-db-read", "bundle": str(root)})
    db = open_db(root, events, run.id)
    server = FastMCP("SpriteForge game record", log_level="ERROR")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @server.tool(annotations=read)
    def game_index(dir: str = "") -> str:
        """The bundle's index (or a section's: design, entities, art, art/assets, code, code/modules, qa, qa/tests, builds, decisions, story, characters, world): every concept with a one-line description and its link. Start here."""
        return db.index(dir.strip("/"))

    @server.tool(annotations=read)
    def game_search(query: str, budget: int = 800) -> list[dict]:
        """Concepts matching a query, best first: id, title, trust tier (unverified, human-reviewed), status and a short snippet, within about `budget` tokens. Then read one with game_show."""
        snippets = db.retrieve(query, token_budget=max(64, min(budget, 4000)), relative_score=0)
        return [s.model_dump(include={"id", "title", "trust_tier", "status", "text"})
                for s in snippets]  # fmt: skip

    @server.tool(annotations=read)
    def game_show(id: str) -> str:
        """One concept in full: YAML frontmatter (type, title, description, resource, tags, sources, generated, verified, status) and its Markdown body. `id` is its path without .md, e.g. entities/hero or code/modules/game."""
        return serialize(db.show(id.strip("/").removesuffix(".md")))

    @server.tool(annotations=read)
    def game_context(focus: str = "", budget: int = 2000) -> str:
        """What a designer needs for a task (e.g. 'level 2: a harder cave'): the game, its rules, the story when there is one, the player and the art style, then the concepts the focus names, in about `budget` tokens."""
        return context_pack(db, focus, max(200, min(budget, 8000)))

    return server
