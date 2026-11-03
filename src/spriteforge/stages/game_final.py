"""G5, the final review of the whole game (L2.6), after the human approves a build at G4.

  game/final/vN-lessons.json/.md  Librarian: lessons from the run (facts code recorded: failed
                             attempts, QA failures, art review issues, levels, the human's
                             words, cost), shown at G5
  game/final/vN-review.json  code: the whole game for G5: every level (bot run, preview,
                             screenshot, its QA tests), QA by kind, art, cost, the record
  gate "final"               G5: text -> the same as G4 feedback (the Director, a new build,
                             then G4 again); empty -> approval.json, the whole record promoted
                             to human-reviewed, the lessons written to the shared memory
                             (lessons/, generated: a human promotes them later), and
                             game/vN/export.zip (the build, its record, its tests)
"""

import re
from datetime import UTC, datetime

from spriteforge.core.gates import Gates, new_gate
from spriteforge.game.export import export_zip
from spriteforge.game.gamedb import promote_all, sync_record
from spriteforge.game.lessons import (
    ACTOR,
    LessonRecord,
    Lessons,
    fallback_lessons,
    lesson_entries,
    lessons_markdown,
    lessons_prompt,
    run_facts,
)
from spriteforge.game.pipeline import call_summary
from spriteforge.game.qa import QASuite
from spriteforge.memory.okf.store import KnowledgeStore

from .game_record import REPORT, decisions, run_record

FINAL = "game/final"


def lessons_name(version: int) -> str:
    return f"{FINAL}/v{version}-lessons.json"


def reports(ctx) -> list[dict]:
    found = {int(m[1]): n for n in ctx.artifacts if (m := REPORT.fullmatch(n))}
    return [ctx.read(found[v]) for v in sorted(found)]


def ensure_lessons(ctx, version: int) -> LessonRecord:
    """The Librarian's lessons from the whole run up to build vN (once per build shown at G5).
    Lessons are advice: a failed call falls back to the lessons code writes."""
    name = lessons_name(version)
    if name not in ctx.artifacts:
        ctx.log("Librarian")("writing what this game teaches the next one")
        report = ctx.read(f"game/v{version}/report.json")
        facts = run_facts(reports(ctx), decisions(ctx), ctx.brief.genre,
                          call_summary(ctx.store, ctx.run_id))  # fmt: skip
        offline, by = fallback_lessons(facts), ctx.models.name
        try:
            answer = ctx.models.ask("Librarian", lessons_prompt(report["title"], ctx.brief.genre, facts),
                                    Lessons, offline, "lessons")  # fmt: skip
        except Exception as exc:  # noqa: BLE001 -- lessons never block the final review
            first = str(exc).strip().splitlines()[0][:160] if str(exc).strip() else ""
            answer, by = offline, f"offline lessons (the Librarian's call failed: {type(exc).__name__}: {first})"
        record = LessonRecord(lessons=answer.lessons, facts=facts, by=by, game=report["title"],
                              genre=ctx.brief.genre, build=version)  # fmt: skip
        ctx.save(name, record.model_dump(), "Librarian")
        ctx.save(name.removesuffix(".json") + ".md", lessons_markdown(record).encode(), "Librarian",
                 "text/markdown")  # fmt: skip
    return LessonRecord.model_validate(ctx.read(name))


def qa_kind(test, levels: int) -> str:
    if test.start == "title":
        return "menus and flow"
    return f"level {test.level}" if levels > 1 else "play"


def final_summary(ctx, version: int) -> dict:
    """What G5 shows about the whole game, from build vN's artifacts and the record."""
    prefix = f"game/v{version}"
    report = ctx.read(f"{prefix}/report.json")
    levels = report.get("levels") or []
    results = {t["name"]: t for t in (report.get("qa") or {}).get("tests", [])}
    suite_name = f"{prefix}/qa-tests.json"
    suite = QASuite.model_validate(ctx.read(suite_name)) if suite_name in ctx.artifacts else None
    by_kind: dict[str, dict] = {}
    for test in suite.tests if suite else []:
        if test.name in results:
            row = by_kind.setdefault(qa_kind(test, len(levels)), {"passed": 0, "failed": 0, "failing": []})
            ok = results[test.name]["passed"]
            row["passed" if ok else "failed"] += 1
            row["failing"] += [] if ok else [test.name]
    named = lambda n: n if n in ctx.artifacts else None
    level_rows = [{
        "n": lv["n"], "name": lv["name"], "cleared": lv["cleared"], "ended": lv.get("ended"),
        "game_seconds": lv.get("game_seconds"), "difficulty": lv.get("difficulty"),
        "play": lv.get("play"), "preview": named(f"{prefix}/levels/level-{lv['n']}.png"),
        "screenshot": named(f"{prefix}/levels/shot-{lv['n']}.png"),
        "qa": by_kind.get(f"level {lv['n']}", {"passed": 0, "failed": 0, "failing": []}),
    } for lv in levels]  # fmt: skip
    sources: dict[str, int] = {}
    for a in report.get("art", []):
        kind = re.sub(r"\s*\(.*\)$", "", a["source"]) + (" (locked)" if "locked" in a["source"] else "")
        sources[kind] = sources.get(kind, 0) + 1
    entries = [e for e in ctx.db.scan()[0] if e.metadata.status != "deprecated"]
    sections: dict[str, int] = {}
    tiers: dict[str, int] = {}
    for e in entries:
        sections[e.id.split("/")[0]] = sections.get(e.id.split("/")[0], 0) + 1
        tiers[e.metadata.trust_tier] = tiers.get(e.metadata.trust_tier, 0) + 1
    qa = report.get("qa") or {}
    review = report.get("art_review") or {}
    return {
        "version": version,
        "title": report["title"],
        "play": report.get("play"),
        "passed": report["passed"],
        "builds": len(reports(ctx)),
        "levels": level_rows,
        "qa": {"passed": qa.get("passed", 0), "failed": qa.get("failed", 0), "by_kind": by_kind},
        "art": {"entities": len(report.get("art", [])), "sources": sources,
                "review_scores": review.get("scores") or {}, "menus": bool(report.get("menus"))},
        "cost": call_summary(ctx.store, ctx.run_id),
        "record": {"concepts": len(entries), "sections": dict(sorted(sections.items())),
                   "trust": tiers, "path": str(ctx.db.root)},
        "lessons": lessons_name(version),
    }  # fmt: skip


def verdict(summary: dict) -> str:
    parts = []
    if levels := summary["levels"]:
        parts.append(f"{sum(lv['cleared'] for lv in levels)}/{len(levels)} levels cleared by the bot")
    qa = summary["qa"]
    if qa["passed"] + qa["failed"]:
        parts.append(f"QA {qa['passed']}/{qa['passed'] + qa['failed']}")
    parts.append(f"{summary['builds']} build(s), about ${summary['cost']['estimated_cost_usd']}")
    return "; ".join(parts)


def open_final_gate(ctx, version: int) -> None:
    """G5 for build vN: the lessons first, then the summary, the levels, the art and the record."""
    lessons = ensure_lessons(ctx, version)
    prefix = f"game/v{version}"
    summary_name = f"{FINAL}/v{version}-review.json"
    if summary_name not in ctx.artifacts:
        ctx.save(summary_name, final_summary(ctx, version), "Director")
    summary = ctx.read(summary_name)
    names = [summary_name, lessons_name(version)]
    for lv in summary["levels"]:
        names += [n for n in (lv["preview"], lv["screenshot"]) if n]
    names += [n for n in (f"{prefix}/demo.mp4", f"{prefix}/poster.png") if n in ctx.artifacts][:1]
    names += [n for n in (f"{prefix}/art.png", f"{prefix}/motion.gif", f"{prefix}/menus.png",
                          f"{prefix}/test_game_qa.py") if n in ctx.artifacts]  # fmt: skip
    names.append(f"{prefix}/report.json")
    title = (
        f"G5 Final review (build v{version}: {summary['title']}): {verdict(summary)}. The whole "
        f"game, its record ({summary['record']['concepts']} concepts) and {len(lessons.lessons)} "
        "lesson(s) the Librarian wrote. Leave feedback empty and submit to finish: the record "
        "becomes human-reviewed, the lessons go to the shared memory for you to promote later, "
        "and you get one zip with the game, its record and its tests. Or type what to change (a "
        "new build, then G4 again)."
    )
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate("final", title, [ctx.artifacts[n].id for n in dict.fromkeys(names)],
                 empty_feedback_approves=True),  # fmt: skip
    )


def write_lessons(ctx, record: LessonRecord) -> dict:
    """The lessons into the shared memory (generated, unverified), once per run."""
    marker = f"{FINAL}/lessons-written.json"
    if marker in ctx.artifacts:
        return ctx.read(marker)
    memory = KnowledgeStore(ctx.store.path.parent / "memory", ctx.store, ctx.run_id)
    entries = lesson_entries(record, ctx.run_id, datetime.now(UTC))
    try:
        memory.write_batch(entries, f"Lessons from {record.game} (run {ctx.run_id[:8]})", actor=ACTOR)
        result = {"ids": [e.id for e in entries], "memory": str(memory.root), "error": ""}
    except Exception as exc:  # noqa: BLE001 -- the game is finished either way; say why not
        result = {"ids": [], "memory": str(memory.root),
                  "error": f"{type(exc).__name__}: {str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ''}"}  # fmt: skip
    ctx.save(marker, result, "Librarian")
    return result


def finish(ctx, version: int, gate_id: str) -> None:
    """G5 approved: the game is done."""
    prefix = f"game/v{version}"
    report = ctx.read(f"{prefix}/report.json")
    db = ctx.db
    sync_record(db, run_record(ctx), f"Game record: G5 approved the whole game (build v{version})")
    promote_all(db, f"G5 approved: the whole game, build v{version}")
    record = ensure_lessons(ctx, version)
    written = write_lessons(ctx, record)
    archive = f"{prefix}/export.zip"
    if archive not in ctx.artifacts:
        data = export_zip(ctx.root / prefix / "build", db.root, report["title"], version,
                          lessons_markdown(record))  # fmt: skip
        ctx.save(archive, data, "Director", "application/zip")
    ctx.save(
        "approval.json",
        {
            "gate": gate_id,
            "mode": "game",
            "version": version,
            "design": f"game/design/v{report['design_version']}.json",
            "build": f"{prefix}/build",
            "code": f"{prefix}/code.json",
            "report": f"{prefix}/report.json",
            "passed": report["passed"],
            "export": archive,
            "lessons": written["ids"],
            "lessons_error": written["error"],
        },
        "Director",
    )
    ctx.store.advance(ctx.run_id, ctx.run.cursor, "G5 approved; the game is finished", final=True)
