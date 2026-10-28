"""Load only the authorized credential into the process environment."""

import os
from pathlib import Path

from dotenv import dotenv_values


def load_credentials(path: Path = Path(".env")) -> None:
    if os.environ.get("META_API_KEY") or not path.is_file():
        return
    key = dotenv_values(path, encoding="utf-8-sig", interpolate=False).get("META_API_KEY")
    if key:
        os.environ["META_API_KEY"] = key
