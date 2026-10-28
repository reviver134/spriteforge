"""Small workspace-confined example server; no write or command-execution tools."""

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def create_server(root: Path):
    root = root.resolve()
    server = FastMCP("SpriteForge filesystem read", log_level="ERROR")

    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    )
    def read_text(path: str) -> str:
        """Read a UTF-8 file under the configured root (maximum 256 KB)."""
        target = (root / path).resolve()
        if not target.is_relative_to(root) or any(
            p.startswith(".") for p in target.relative_to(root).parts
        ):
            raise ValueError("File must be a non-hidden path inside the root")
        if target.stat().st_size > 256 * 1024:
            raise ValueError("File too large")
        return target.read_text(encoding="utf-8")

    return server


if __name__ == "__main__":
    create_server(Path(sys.argv[1])).run(transport="stdio")
