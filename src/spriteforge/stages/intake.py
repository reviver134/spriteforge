"""Validated brief and role proposals grounded in the local genre/style corpus."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    screen: tuple[Literal[320], Literal[180]] = (320, 180)


class Questions(BaseModel):
    questions: list[str] = Field(min_length=2, max_length=4)


class StyleOption(BaseModel):
    name: str
    description: str
    colors: list[str] = Field(min_length=4, max_length=16)

    @field_validator("colors")
    @classmethod
    def validate_colors(cls, colors):
        return [hex_color(c) for c in colors]


class Options(BaseModel):
    options: list[StyleOption] = Field(min_length=2, max_length=3)


def propose(provider, role, prompt, schema):
    instructions = load_roles()[role]
    return chat_json(
        provider,
        ChatRequest(
            messages=[
                Message(role="system", content=str(instructions.model_dump())),
                Message(role="user", content=prompt),
            ]
        ),
        schema,
    )


def section(body, heading):
    part = body.split("# " + heading + "\n", 1)
    return part[1].split("\n# ", 1)[0].strip() if len(part) == 2 else ""


def seeded_questions(entry):
    return [
        re.sub(r"^\d+\.\s*", "", line)
        for line in section(entry.body, "Adaptive questions").splitlines()
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


def brief_form(entry):
    defaults = GameBrief()
    properties = {
        name: field(name.replace("_", " ").title(), value)
        for name, value in defaults.model_dump().items()
        if name not in {"genre", "answers", "screen"}
    }
    properties["perspective"]["suggestions"] = ["top-down", "side-scroll", "isometric"]
    properties["controls"]["suggestions"] = ["keyboard", "gamepad", "touch"]
    properties["difficulty"]["suggestions"] = ["Forgiving", "Balanced", "Challenging"]
    suggestions = {
        "environment": ["Mossy ruins", "Cloud city", "Cozy woodland"],
        "mood": ["Bright and cheerful", "Muted and mysterious", "Dark neon"],
        "character": ["Brave explorer", "Small robot", "Friendly woodland creature"],
        "core_loop": [
            "Explore, fight, collect keys",
            "Jump, dodge, reach the exit",
            "Plant, harvest, upgrade",
        ],
        "session_length": ["3 minutes", "5 minutes", "15 minutes"],
        "references": [
            "No specific reference games",
            "Zelda-like exploration",
            "Mario-like movement",
        ],
    }
    for name, choices in suggestions.items():
        properties[name]["suggestions"] = choices
    properties["core_loop"]["default"] = section(entry.body, "Core mechanics template")
    properties["perspective"]["default"] = (
        "side-scroll" if "Side-" in section(entry.body, "Perspective and camera") else "top-down"
    )
    for i, question in enumerate(seeded_questions(entry)):
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
