"""Validated brief and role proposals grounded in the local genre/style corpus."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from spriteforge.agents.models import Contract
from spriteforge.agents.roles import load_roles
from spriteforge.pixel.schemas import hex_color
from spriteforge.providers.base import ChatRequest, Message, chat_json


class GameBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genre: str = "genres/top-down-dungeon-crawler"
    perspective: str = "top-down"
    environment: str = "A mossy ruined dungeon"
    mood: str = "Mysterious, readable greens and warm amber"
    character: str = "A small brave explorer"
    core_loop: str = "Explore rooms, dodge enemies, collect keys and reach the exit"
    difficulty: str = "Forgiving"
    session_length: str = "5 minutes"
    controls: str = "keyboard"
    references: str = "No specific reference games"
    answers: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    screen: tuple[Literal[320], Literal[180]] = (320, 180)


class GenrePick(Contract):
    """The Game Designer's reading of the prompt: a genre in its own words plus seeded references."""

    genre: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=1)
    closest: list[str] = Field(max_length=3)
    questions: list[str] = Field(min_length=2, max_length=6)


BRIEF_FIELDS = (
    "perspective",
    "environment",
    "mood",
    "character",
    "core_loop",
    "difficulty",
    "session_length",
    "controls",
    "references",
)


class BriefDraft(Contract):
    """G1 brief defaults drafted from the prompt, so the form starts from the human's idea."""

    perspective: str
    environment: str
    mood: str
    character: str
    core_loop: str
    difficulty: str
    session_length: str
    controls: str
    references: str


class Questions(Contract):
    questions: list[str] = Field(min_length=2, max_length=4)


class StyleOption(Contract):
    name: str
    description: str
    colors: list[str] = Field(min_length=4, max_length=16)

    @field_validator("colors")
    @classmethod
    def validate_colors(cls, colors):
        return [hex_color(c) for c in colors]


class Options(Contract):
    options: list[StyleOption] = Field(min_length=2, max_length=3)


def propose(provider, role, prompt, schema):
    instructions = load_roles()[role]
    return chat_json(
        provider,
        ChatRequest(
            messages=[
                Message(
                    role="system",
                    content=str(instructions.model_dump())
                    + "\nHuman notes are part of the brief and override untouched template defaults. Ask about genuine conflicts instead of discarding the notes.",
                ),
                Message(role="user", content=prompt),
            ]
        ),
        schema,
    )


def section(body, heading):
    part = body.split("# " + heading + "\n", 1)
    return part[1].split("\n# ", 1)[0].strip() if len(part) == 2 else ""


def seeded_questions(entry):
    return seeded_questions_from(entry.body)


def seeded_questions_from(body):
    return [
        re.sub(r"^\d+\.\s*", "", line)
        for line in section(body, "Adaptive questions").splitlines()
        if line.strip()
    ]


def field(title, default="", choices=None):
    return {
        "type": "string",
        "title": title,
        "default": default,
        "suggestions": choices
        or [default or "Use the genre convention", "Keep it simple for the first build"],
    }


STOP_WORDS = {"a", "an", "and", "the", "of", "to", "in", "on", "with", "where", "game", "will"}


def words(text):
    return set(re.findall(r"[a-z]+", text.lower())) - STOP_WORDS


def genre_prompt(goal, genres):
    catalog = "\n".join(f"- {e.id}: {e.title}. {e.metadata.description}" for e in genres)
    return (
        "Read the human's game idea and name its genre in the idea's own terms (for example "
        "'Top-down arena survival'). Use a seeded genre's exact title only when the idea fits "
        "that genre's rules; otherwise name a new genre. description: camera, controls, core "
        "loop and how a session ends, in 2-4 sentences, taken from the idea. closest: up to 3 "
        "seeded genre ids that are useful references (may be empty). questions: 2-6 questions "
        "about this genre that the idea leaves open.\nIdea: "
        + goal
        + "\nSeeded genres:\n"
        + catalog
    )


def guess_genre(goal, genres):
    """Offline pick: the seeded genre sharing the most words with the idea."""
    fallback = next((e for e in genres if e.id.endswith("/top-down-dungeon-crawler")), genres[0])
    if not goal.strip():
        best = [fallback]
    else:
        idea = words(goal)
        scored = sorted(
            genres,
            key=lambda e: -len(idea & words(e.title + " " + e.metadata.description)),
        )
        best = [e for e in scored if idea & words(e.title + " " + e.metadata.description)][:3]
    if not best:
        return GenrePick(
            genre="Custom game",
            description=goal,
            closest=[],
            questions=["What does the player do most of the time?", "How does a session end?"],
        )
    return GenrePick(
        genre=best[0].title,
        description=best[0].metadata.description,
        closest=[e.id for e in best],
        questions=seeded_questions(best[0])[:6] or ["How does a session end?", "What is hard?"],
    )


def genre_form(pick, genres, note):
    titles = {e.id: e.title for e in genres}
    suggestions = [pick.genre] + [titles[i] for i in pick.closest if i in titles]
    suggestions += [t for t in titles.values() if t not in suggestions]
    return {
        "type": "object",
        "properties": {
            "genre": {
                "type": "string",
                "title": "Genre",
                "description": f"{note} Suggested from your idea: {pick.description}",
                "default": pick.genre,
                "suggestions": suggestions,
            }
        },
    }


def match_genre(text, genres):
    """A typed or picked genre that names a seeded genre (by id or title), else None."""
    key = text.strip().lower()
    return next((e for e in genres if key in {e.id.lower(), e.title.lower()}), None)


def custom_genre_body(pick, genres):
    """A genre written for this idea, in the seeded entries' section format."""
    refs = [e for e in genres if e.id in pick.closest]
    return (
        f"# Core mechanics template\n{pick.description}\n\n# Adaptive questions\n"
        + "".join(f"{i}. {q}\n" for i, q in enumerate(pick.questions, 1))
        + "\n# Related seeded genres (references only; the idea's rules win)\n"
        + ("".join(f"* {e.title}: {e.metadata.description}\n" for e in refs) or "* none\n")
    )


def brief_prompt(goal, genre_title, body):
    return (
        "Draft the G1 brief fields from the human's idea and the chosen genre. Keep every "
        "detail the idea states (camera, session length, enemies, pickups, upgrades, how it "
        "gets harder); fill only what it leaves open with the simplest choice for the genre. "
        "Each field is one short line.\nIdea: " + goal + f"\nGenre: {genre_title}\n" + body
    )


def default_draft(body):
    """Offline brief defaults: the generic brief, with the genre's loop and camera."""
    defaults = GameBrief().model_dump(include=set(BRIEF_FIELDS))
    defaults["core_loop"] = section(body, "Core mechanics template") or defaults["core_loop"]
    if "Side-" in section(body, "Perspective and camera"):
        defaults["perspective"] = "side-scroll"
    return BriefDraft(**defaults)


def brief_form(body, draft):
    properties = {
        name: field(name.replace("_", " ").title(), value)
        for name, value in draft.model_dump().items()
    }
    suggestions = {
        "perspective": ["top-down", "side-scroll", "isometric"],
        "controls": ["keyboard", "gamepad", "touch"],
        "difficulty": ["Forgiving", "Balanced", "Challenging", "Starts easy, gets hard"],
        "session_length": ["3 minutes", "5 minutes", "10 minutes", "15 minutes"],
        "references": ["No specific reference games"],
    }
    for name, rule in properties.items():
        rule["suggestions"] = list(dict.fromkeys([rule["default"], *suggestions.get(name, [])]))
    for i, question in enumerate(seeded_questions_from(body)):
        properties[f"seed_{i}"] = field(question)
    return {"type": "object", "properties": properties}


def followups(brief):
    return Questions(
        questions=[
            f"For {brief.character} in {brief.environment}, what should the first discovery be?",
            f"With {brief.controls} controls and {brief.difficulty.lower()} difficulty, what assistance would help?",
            f"What should end a {brief.session_length} session of '{brief.core_loop}'?",
        ]
    )


def default_options(brief, entries):
    palettes = {
        "dungeon-muted": ["#181825", "#454452", "#848078", "#CFBD91", "#4A994A", "#E7AD46"],
        "bright-adventure": ["#172B49", "#805538", "#CF9656", "#66AA55", "#82C8DB", "#FFF0BA"],
        "limited-monochrome": ["#101820", "#486858", "#90A880", "#E0E8B0"],
        "night-neon": ["#101827", "#29354C", "#536783", "#65DFEB", "#F5BA54", "#E75B65"],
    }
    return Options(
        options=[
            StyleOption(
                name=e.title,
                description=e.metadata.description or e.title,
                colors=palettes.get(e.id.split("/")[-1], palettes["bright-adventure"]),
            )
            for e in entries[:3]
        ]
    )
