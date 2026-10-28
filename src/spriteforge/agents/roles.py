"""Editable role definitions, loaded afresh when a run starts."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    model: str = Field(min_length=1)
    allowed_tools: list[str]
    allowed_skills: list[str]
    memory_scopes: list[Literal["project", "private"]]
    max_steps: int = Field(ge=1, le=100)
    prompt: str = Field(min_length=1)


def load_roles(directory: Path | None = None) -> dict[str, Role]:
    roles = {}
    for path in sorted((directory or Path(__file__).with_name("roles")).glob("*.md")):
        _, frontmatter, prompt = path.read_text(encoding="utf-8").split("---", 2)
        role = Role.model_validate({**yaml.safe_load(frontmatter), "prompt": prompt.strip()})
        if role.name in roles:
            raise ValueError(f"Duplicate role: {role.name}")
        roles[role.name] = role
    if not roles:
        raise ValueError("No role definitions found")
    return roles
