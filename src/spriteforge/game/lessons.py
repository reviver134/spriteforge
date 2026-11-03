"""Lessons from a finished game (L2.6): the Librarian reads what happened and writes what the
next game should do differently, as generated knowledge in the shared memory (`lessons/`).

Code gathers the facts from the run (no model call): every build's attempts and what failed,
the QA tests that failed and then passed, the art review's issues and fixes, the levels the bot
could not clear and what code repaired in them, the human's words at each gate (redacted) and
the cost. The Librarian (Muse Spark) turns them into 1-6 lessons that hold for other games of
the genre; offline, code writes them from the same facts.

Lessons are shown at G5 and written to the shared memory when the human approves the game.
They stay generated (unverified) until a human promotes one (`spriteforge memory promote`):
an agent never claims human verification (OKF Agent Memory Convention).
"""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from spriteforge.agents.models import Contract
from spriteforge.memory.okf.models import Entry, Metadata, Source

from .bundle import safe_text, slug

ACTOR = "spriteforge/librarian"
Topic = Literal["design", "levels", "art", "motion", "code", "qa", "menus", "process"]
MAX_LESSONS = 6


class Lesson(Contract):
    title: str = Field(description="the lesson in a few words, e.g. 'Gate walls need a crate first'")
    topic: Topic = Field(description="what part of making a game it is about")
    lesson: str = Field(description="what to do in the next game, 1-3 sentences, general enough "
                                    "for another game of the genre")  # fmt: skip
    evidence: str = Field(description="what happened in this run that shows it (from the facts)")

    @model_validator(mode="after")
    def check(self):
        self.title = re.sub(r"\s+", " ", self.title).strip()[:80]
        self.lesson, self.evidence = self.lesson.strip()[:600], self.evidence.strip()[:400]
        if not self.title or not self.lesson:
            raise ValueError("a lesson needs a title and what to do")
        return self


class Lessons(Contract):
    lessons: list[Lesson]

    @model_validator(mode="after")
    def check(self):
        if not 1 <= len(self.lessons) <= MAX_LESSONS:
            raise ValueError(f"write 1 to {MAX_LESSONS} lessons")
        titles = [lesson.title.casefold() for lesson in self.lessons]
        if len(set(titles)) != len(titles):
            raise ValueError("lesson titles must be unique")
        return self


class LessonRecord(Contract):
    """game/final/lessons.json: the lessons, the facts they came from and who wrote them."""

    lessons: list[Lesson]
    facts: list[str]
    by: str
    game: str
    genre: str
    build: int


# --- the facts --------------------------------------------------------------------------------


def clip(text: str, n: int = 160) -> str:
    text = re.sub(r"\s+", " ", safe_text(str(text))).strip()
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def build_facts(report: dict) -> list[str]:
    """What one build's report says went wrong or needed work."""
    v, out = report["version"], []
    attempts = report.get("attempts", [])
    failed = [a for a in attempts if not a.get("passed")]
    if failed:
        stages = ", ".join(sorted({str(a.get("stage")) for a in failed}))
        first = clip(failed[0].get("summary", ""), 220)
        out.append(f"build v{v}: {len(attempts)} attempts; failed at {stages}; first failure: {first}")
    for p in report.get("problems", [])[:3]:
        out.append(f"build v{v}: problem left: {clip(p)}")
    for c in report.get("checks", []):
        if not c.get("ok"):
            out.append(f"build v{v}: check `{c['event']}` failed ({c.get('count', 0)}/{c.get('min_count', '?')})")
    for lv in report.get("levels", []):
        if not lv.get("cleared"):
            out.append(f"build v{v}: the bot did not clear level {lv['n']} ({lv.get('ended')})")
        for p in lv.get("problems", [])[:2]:
            out.append(f"build v{v}: level {lv['n']} problem: {clip(p)}")
    for t in (report.get("qa") or {}).get("tests", []):
        if not t.get("passed"):
            out.append(f"build v{v}: QA test '{t['name']}' failed: {clip(t.get('failure', ''))}")
    review = report.get("art_review") or {}
    for i in review.get("issues", []):
        if i.get("severity") in ("blocking", "major"):
            out.append(f"build v{v}: art review ({i['severity']}) {i['entity']}: {clip(i['problem'])} "
                       f"[{i.get('status', '')}]")  # fmt: skip
    for w in report.get("warnings", [])[:3]:
        out.append(f"build v{v}: art warning: {clip(w)}")
    menus = report.get("menus") or {}
    for p in menus.get("problems", [])[:2]:
        out.append(f"build v{v}: menu problem: {clip(p)}")
    return out


def run_facts(reports: list[dict], decisions: list, genre: str, cost: dict) -> list[str]:
    """Every build's facts, then the human's decisions and the cost. `decisions`: the bundle's
    Decision records (gate, outcome, words, changed)."""
    facts = [f"genre {genre}; {len(reports)} build(s)"]
    if reports and reports[-1].get("levels"):
        facts[0] += f"; {len(reports[-1]['levels'])} levels as data"
    for r in reports:
        facts += build_facts(r)
    for d in decisions:
        if d.words.strip() or d.outcome != "approved":
            words = f": \"{clip(d.words, 200)}\"" if d.words.strip() else ""
            facts.append(f"human at {d.gate} ({d.outcome}){words} -> {clip(d.changed, 160)}")
    facts.append(f"cost: {cost.get('chat', 0)} Spark and {cost.get('image', 0)} Muse Image calls, "
                 f"about ${cost.get('estimated_cost_usd', 0)}")  # fmt: skip
    return facts[:60]


# --- the Librarian ----------------------------------------------------------------------------


def lessons_prompt(title: str, genre: str, facts: list[str]) -> str:
    lines = "\n".join(f"- {f}" for f in facts)
    return f"""A game was just finished and approved. Write lessons for the next games.

Read the facts (what code recorded while the agents made "{title}", a {genre} game). Write 1 to
{MAX_LESSONS} lessons that would have saved a build, a repair, a failed test or a human's
correction. Each lesson:
* says what to do next time, in 1-3 sentences, for any game of this genre (no names, places or
  story from this game; no numbers it does not need);
* cites the fact that shows it in `evidence` (from the facts below, not invented);
* has one topic: design, levels, art, motion, code, qa, menus or process.
Prefer what the human had to correct and what failed more than once. Do not praise, do not
repeat a fact as a lesson, and do not claim anything was verified by a human.

Facts:
{lines}"""


# The first phrase a fact contains decides its topic (a QA test of a level is about QA).
TOPIC_OF = (("QA test", "qa"), ("art review", "art"), ("art warning", "art"),
            ("menu problem", "menus"), ("level", "levels"), ("check", "code"),
            ("attempts", "code"))  # fmt: skip


def fallback_lessons(facts: list[str]) -> Lessons:
    """Offline: one lesson per kind of trouble the facts show, and one on the human's words."""
    found: dict[str, list[str]] = {}
    for f in facts[1:]:
        if f.startswith(("human at", "cost:")):
            continue
        topic = next((t for word, t in TOPIC_OF if word in f), "process")
        found.setdefault(topic, []).append(f)
    text = {
        "levels": ("Check every level against the player's movement before building",
                   "Have code prove each level is reachable and clearable before the Programmer "
                   "builds it, and keep levels that failed in a build on the next build's checks."),
        "qa": ("Write QA tests the game can pass with real keys",
               "QA tests found what the bot could not; keep the failing ones in the regression "
               "suite and give the Programmer the failing step, not only the test name."),
        "art": ("Review the art set before the playtest gate",
                "The art review flagged sprites a player could not read; describe each entity's "
                "silhouette and colours apart from the others when the art is planned."),
        "menus": ("Let code fit the menus", "Menu problems came back from the designer; keep "
                  "labels short and actions from the known list."),
        "code": ("Tell the Programmer the checks up front",
                 "The first code failed its checks; list every event the checks count and the "
                 "SDK's contract in the first prompt so fewer repairs are needed."),
    }
    lessons = [Lesson(title=text[t][0], topic=t, lesson=text[t][1], evidence="; ".join(fs[:2]))
               for t, fs in found.items() if t in text]  # fmt: skip
    asked = [f for f in facts if f.startswith("human at") and "changes requested" in f]
    if asked:
        lessons.append(Lesson(
            title="Plan for what the human asked to change", topic="process",
            lesson="The human corrected the game at a gate; ask about this in the G1 questions "
                   "or cover it in the plan before the first build.",
            evidence="; ".join(asked[:2])))  # fmt: skip
    if not lessons:
        lessons.append(Lesson(title="A clean run", topic="process",
                              lesson="Nothing failed; keep the same plan, checks and art direction "
                                     "for the next game of this genre.",
                              evidence=facts[0]))  # fmt: skip
    return Lessons(lessons=lessons[:MAX_LESSONS])


# --- into the shared memory -------------------------------------------------------------------


def lesson_entries(record: LessonRecord, run_id: str, now: datetime | None = None) -> list[Entry]:
    """One OKF concept per lesson under `lessons/`, generated and unverified."""
    now = now or datetime.now(UTC)
    base = f"lessons/{slug(record.game)[:40]}-{run_id[:8]}"
    entries = []
    for k, lesson in enumerate(record.lessons, 1):
        body = f"""
# Lesson
{safe_text(lesson.lesson)}

# Evidence
{safe_text(lesson.evidence) or "-"}

# Where it comes from
The game "{safe_text(record.game)}" ({record.genre}), run `{run_id}`, build v{record.build},
approved at G5 on {now.date().isoformat()}. Written by the Librarian ({record.by}) from facts
code recorded during the run. Generated: not reviewed by a human until promoted with
`spriteforge memory promote {base}-{k} --by <you>`.
"""
        metadata = Metadata(
            type="Lesson",
            title=safe_text(lesson.title),
            description=re.sub(r"\s+", " ", safe_text(lesson.lesson))[:300],
            tags=sorted({"lesson", lesson.topic, record.genre.rsplit("/", 1)[-1]}),
            sources=[Source(resource=f"spriteforge://runs/{run_id}/game/v{record.build}/report.json",
                            title=f"build v{record.build} of {safe_text(record.game)}")],  # fmt: skip
        )
        entries.append(Entry(id=f"{base}-{k}", metadata=metadata, body=body.strip() + "\n"))
    return entries


def reviewed_lesson(entry: Entry) -> bool:
    return (entry.id.startswith("lessons/") and entry.metadata.trust_tier == "human-reviewed"
            and entry.metadata.status != "deprecated")  # fmt: skip


def past_lessons(memory, genre: str, request: str, budget: int = 500) -> str:
    """The human-reviewed lessons of earlier games most relevant to this request, for the game
    plan (bounded and event-logged by the store's retrieval). Generated lessons wait for a
    human to promote them; until then no agent reads them."""
    if not (memory.root / "lessons").is_dir():
        return ""
    kind = genre.rsplit("/", 1)[-1]
    query = f"{kind.replace('-', ' ')} {request}"[:400]
    found = memory.retrieve(query, token_budget=budget, min_score=0, relative_score=0,
                            only=lambda e: reviewed_lesson(e) and kind in e.metadata.tags)  # fmt: skip
    return "\n".join(f"- {s.title}: {s.text.splitlines()[0]}" for s in found)


def lessons_markdown(record: LessonRecord) -> str:
    lines = [f"# Lessons from {record.game}", "",
             f"Written by the Librarian ({record.by}) from the facts below; they go to the shared "
             "memory (`lessons/`) as generated knowledge when G5 approves the game.", ""]  # fmt: skip
    for k, lesson in enumerate(record.lessons, 1):
        lines += [f"## {k}. {lesson.title} ({lesson.topic})", lesson.lesson, "",
                  f"Evidence: {lesson.evidence}", ""]  # fmt: skip
    lines += ["## Facts", *(f"- {f}" for f in record.facts)]
    return "\n".join(lines) + "\n"
