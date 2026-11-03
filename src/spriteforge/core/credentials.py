"""Load only the authorized credential into the process environment."""

import os
from pathlib import Path

from dotenv import dotenv_values

# The repo's own .env (a source checkout), so the key loads wherever SpriteForge is started.
REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


def load_credentials(path: Path | None = None) -> None:
    """META_API_KEY from the environment, else the first .env that has it: `path`, the file
    SPRITEFORGE_ENV_FILE names, or the current folder's then the repo's."""
    if os.environ.get("META_API_KEY"):
        return
    chosen = path or os.environ.get("SPRITEFORGE_ENV_FILE")
    for candidate in [Path(chosen)] if chosen else [Path(".env"), REPO_ENV]:
        if not candidate.is_file():
            continue
        key = dotenv_values(candidate, encoding="utf-8-sig", interpolate=False).get("META_API_KEY")
        if key:
            os.environ["META_API_KEY"] = key
            return
