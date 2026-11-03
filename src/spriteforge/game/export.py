"""The finished game as one zip (L2.6, G5): the build, its record and its tests.

    <slug>/build/                  the playable game (index.html, the SDK, art, levels, menus)
    <slug>/build/tests/            test_game_qa.py: the QA tests (pytest + Playwright)
    <slug>/db/                     the game's record, an OKF v0.2 bundle (index.md first)
    <slug>/lessons.md              what the Librarian learned (when there are lessons)
    <slug>/README.txt              how to play it, run its tests and read its record

Only files are copied: no git history, no databases, nothing outside the two folders.
"""

import io
import zipfile
from pathlib import Path

from .bundle import slug as make_slug

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".sqlite3", ".lock"}

README = """{title}

Made with SpriteForge (Muse Spark and Muse Image), build v{version}.

Play it: serve the build folder and open it in a browser, e.g.
    cd build && python -m http.server 8000      then open http://localhost:8000/index.html

Run its QA tests (real key presses in a headless browser):
    pip install pytest playwright && python -m playwright install chromium
    pytest build/tests

Read its record: db/index.md is an OKF v0.2 bundle (Markdown with YAML frontmatter): the
design, the plan, the levels, the entities and their art, the code modules, the QA tests,
every build and the human's decisions at each gate.
"""


def files_under(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).rglob("*") if p.is_file()
                  and not SKIP_DIRS & set(p.relative_to(root).parts)
                  and p.suffix.lower() not in SKIP_SUFFIXES)  # fmt: skip


def export_zip(build: Path, db: Path, title: str, version: int, lessons: str = "") -> bytes:
    """The zip's bytes: files only, in a fixed order with a fixed date, so the same files make
    the same zip (a run resumed after a failure saves the same artifact)."""
    top = make_slug(title)[:40] or "game"
    out = io.BytesIO()

    def put(name: str, data: bytes) -> None:
        info = zipfile.ZipInfo(f"{top}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, data)

    with zipfile.ZipFile(out, "w") as z:
        put("README.txt", README.format(title=title, version=version).encode())
        if lessons:
            put("lessons.md", lessons.encode())
        for folder, root in (("build", Path(build)), ("db", Path(db))):
            for path in files_under(root):
                put(f"{folder}/{path.relative_to(root).as_posix()}", path.read_bytes())
    return out.getvalue()
