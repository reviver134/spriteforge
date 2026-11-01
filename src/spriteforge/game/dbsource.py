"""A game's record read back from files: the art of a build folder, a `game make` folder.

The bundle is always written from what a build left on disk (or in the run's artifacts, see
`stages/game_record.py`), never from objects in memory, so `spriteforge game db sync` rebuilds
the same bundle later (an old run, a repair).
"""

import json
import re
from pathlib import Path

from .bundle import ArtInfo, Build, Decision, GameRecord, art_info, portrait_of
from .codegen import CodeFile, GameCode
from .design import GameDesign
from .qa import QASuite, QATests
from .story import StoryBible

SDK_SCRIPTS = {"phaser.min.js", "sf.js", "assets.js"}


def build_manifest(build: Path) -> dict:
    """The build's assets.json (sprites and images with their frames), {} without a build."""
    path = build / "assets.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def read_art(sheets: dict[str, bytes], manifest: dict, records: dict[str, dict],
             version: int) -> dict[str, ArtInfo]:  # fmt: skip
    """ArtInfo per entity from its sheet, the build manifest (frame size, role) and its
    record (source, version, Muse prompt)."""
    filled = {}
    for eid, sheet in sheets.items():
        meta = (manifest.get("sprites") or {}).get(eid)
        f = portrait_of(sheet, tuple(meta["frame"]) if meta and meta.get("frame") else None)
        image = (manifest.get("images") or {}).get(eid) or {}
        f.meta.update({k: v for k, v in (meta or image).items() if k not in {"file", "frame"}})
        rec = records.get(eid, {})
        f.source, f.record = rec.get("source", ""), rec
        filled[eid] = f
    return art_info(filled, version)


def credits(build: Path) -> list[str]:
    path = build / "CREDITS.txt"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[1:]
    return [line for line in lines if line.strip() and not line.startswith("(generated")]


def build_code(build: Path) -> GameCode | None:
    """The generated scripts of a build folder, in load order (older folders kept no
    code.json)."""
    index = build / "index.html"
    if not index.is_file():
        return None
    scripts = re.findall(r'<script src="([^"]+)"', index.read_text(encoding="utf-8"))
    files = [CodeFile(path=s, content=(build / s).read_text(encoding="utf-8"))
             for s in scripts if s not in SDK_SCRIPTS and (build / s).is_file()]  # fmt: skip
    return GameCode(files=files, notes="") if files else None


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def folder_record(out: Path, request: str | None = None) -> GameRecord:
    """The record of a `game make` folder: design, story (when asked for), the build's art,
    code and QA tests, the build report and the request as the one decision."""
    out = Path(out)
    design = GameDesign.model_validate(load(out / "design.json"))
    story_data = load(out / "story.json")
    summary = load(out / "report.json") or {}
    playtest = load(out / "playtest.json") or {}
    build = out / "build"
    rec = GameRecord(design=design, design_source="design.json")
    if story_data:
        rec.story, rec.story_source = StoryBible.model_validate(story_data), "story.json"
    report = {
        **{k: v for k, v in summary.items() if k not in {"game_db", "recording"}},
        "design_version": 1,
        "checks": playtest.get("checks", summary.get("checks", [])),
    }
    if summary:
        rec.builds = [Build(1, "report.json", report)]
        rec.play = "build/index.html" if summary.get("play") else None
    art_json = load(out / "art.json") or {}
    if (build / "assets").is_dir():
        manifest = build_manifest(build)
        listed = {**manifest.get("sprites", {}), **manifest.get("images", {})}
        prompts = {e["entity"]: e for e in art_json.get("entities", [])}
        sheets = {eid: (build / "assets" / f"{eid}.png").read_bytes() for eid in listed
                  if eid in {e.id for e in design.entities}
                  and (build / "assets" / f"{eid}.png").is_file()}  # fmt: skip
        records = {eid: {"source": listed[eid].get("source", ""),
                         "muse_prompt": (prompts.get(eid) or {}).get("muse_prompt") or ""}
                   for eid in sheets}  # fmt: skip
        rec.art = read_art(sheets, manifest, records, 1)
        rec.screen = tuple(manifest.get("screen") or art_json.get("screen") or (320, 180))
        rec.scale = int(manifest.get("scale") or art_json.get("scale") or 1)
        rec.credits = credits(build)
    code = load(out / "code.json")
    rec.code = GameCode.model_validate(code) if code else build_code(build)
    tests = load(out / "qa-tests.json")
    if tests:
        qa = QATests.model_validate(tests)
        note = (summary.get("qa") or {}).get("note", "")
        rec.qa = QASuite(tests=qa.tests, versions=[1] * len(qa.tests), dropped=[],
                         notes=[note] if note else [])  # fmt: skip
        export = build / "tests" / "test_game_qa.py"
        rec.qa_export = export.read_bytes() if export.is_file() else None
    words = request if request is not None else summary.get("request", "")
    rec.decisions = [Decision(
        gate="the request (spriteforge game make)", outcome="requested", words=words,
        changed="design v1" + (" and its story (asked for with --story)" if rec.story else
                               "; no story (not asked for)"),
        build=1 if summary else None, asked_build=True,
    )]  # fmt: skip
    return rec
