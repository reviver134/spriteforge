"""Editable role definitions, loaded afresh when a run starts."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    model: str = Field(min_length=1)
    allowed_tools: list[str]
    allowed_skills: list[str]
    required_skills: list[str] = Field(default_factory=list)
    memory_scopes: list[Literal["project", "private"]]
    max_steps: int = Field(ge=1, le=100)
    prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def required_are_allowed(self):
        if set(self.required_skills) - set(self.allowed_skills):
            raise ValueError("Required skills must be in the role allowlist")
        return self


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
